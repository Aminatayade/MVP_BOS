/**
 * FILE: features/bim/components/BimModelViewer.tsx
 * DESCRIPTION: Visualiseur 3D du jumeau numérique (Point 6 de la roadmap),
 *              basé sur ThatOpenEngine (@thatopen/components + fragments).
 *              Charge un IFC par discipline (Archi, Élec, CVC...) dans la
 *              même scène three.js -- c'est ce chargement multiple qui
 *              réalise la "fusion visuelle" demandée, sans traitement
 *              serveur : chaque IFC est coordonné sur son système de
 *              coordonnées de site partagé (option `coordinate` de
 *              IfcLoader.load, laissée à sa valeur par défaut).
 */

import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import * as OBC from '@thatopen/components'
import type { FragmentsModel, RaycastResult } from '@thatopen/fragments'
import {
  downloadModelBytes,
  formatMetricValue,
  getBuildingsList,
  getFloorEquipment,
  getFloorRooms,
  getFloors,
  truncateLabel,
  type BuildingOption,
  type FloorOption,
  type ModelSource,
  type RoomOption,
} from '../services/arcadeApi'
import MetricHistoryPanel from './MetricHistoryPanel'
import { useEquipmentTelemetry } from '../hooks/useEquipmentTelemetry'

type Props = {
  sources: ModelSource[]
  // Requise depuis l'isolation par zone du graphe (Phase 1) : /graph/floors
  // (et les autres routes de lecture) ont désormais besoin de `zone` pour
  // savoir quelle base ArcadeDB interroger, pas seulement pour filtrer le
  // sélecteur étage -> pièce affiché avant d'entrer en immersion.
  zone: string
  height?: string | number
  // Sélection croisée avec le graphe (Mode Duo, voir BimExplorer.tsx) :
  // remonte le GlobalId + type IFC (via item.getCategory()) cliqué en 3D au
  // parent...
  onElementSelect?: (globalId: string, ifcType: string) => void
  // ...et reçoit en retour l'élément sélectionné côté graphe (id + type
  // Cytoscape, ex: "Floor"/"Room"/"Equipment") pour réagir différemment
  // selon le type : un Floor déclenche le même plan 2D découpé que le flux
  // "Immersion -> Choisir un étage", les autres types un simple recadrage
  // caméra (voir l'effet plus bas).
  focusElement?: { id: string; type: string } | null
  // Bouton "Coloriser en 3D" de TypologyPanel (Mode Duo) : GlobalId des
  // pièces à peindre + couleur choisie par l'utilisateur (pas de couleur
  // fixe imposée ici). `null` = efface la coloration active.
  colorGroup?: { ids: string[]; color: string } | null
}

type LoadedModel = {
  discipline: string
  model: FragmentsModel
}

// NB : filtrer côté client sur item.getCategory() (essayé précédemment) n'est
// pas fiable -- getCategory() échoue ou renvoie une catégorie vide/générique
// sur une part significative des éléments réels (cf. commentaire plus bas,
// "getCategory()/getAttributes() échouaient..."), y compris de vrais
// équipements : ça masquait la popup d'équipements légitimes. Le filtrage
// "pas de télémétrie -> pas d'erreur" se fait donc côté résultat (le 404
// "introuvable dans le graphe" est traité comme un état normal, voir
// getEquipmentSummary dans arcadeApi.ts), pas en bloquant l'ouverture de la
// popup elle-même.

// POC point 7 de la roadmap (Paul) : référence de l'élément 3D cliqué, pas
// encore reliée à des données temps réel -- juste ce que l'utilisateur a
// sélectionné, positionné à l'écran pour la popup.
type SelectedElement = {
  guid: string
  category: string
  name: string
  x: number
  y: number
}

// ThatOpenEngine's FirstPersonMode ne fait que resserrer l'orbite (distance
// figée à 1) et accélérer le "truck" -- elle ne branche AUCUNE touche
// clavier. Sans ce qui suit, "immersion" ne fait que recentrer la caméra sur
// place, jamais avancer dans le bâtiment : c'est exactement le bug remonté
// ("point d'origine décalé mais toujours vu de l'extérieur"). Ces trois
// constantes pilotent le WASD ajouté à la main ci-dessous.
const MOVEMENT_KEYS = new Set([
  'KeyW', 'KeyA', 'KeyS', 'KeyD',
  'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight',
  'Space', 'ShiftLeft', 'ShiftRight',
])
const MOVE_SPEED = 3 // unités de scène (~mètres) par seconde

export default function BimModelViewer({
  sources,
  zone,
  height = '600px',
  onElementSelect,
  focusElement,
  colorGroup,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const componentsRef = useRef<OBC.Components | null>(null)
  const worldRef = useRef<OBC.SimpleWorld<OBC.SimpleScene, OBC.OrthoPerspectiveCamera, OBC.SimpleRenderer> | null>(null)
  const ifcLoaderRef = useRef<OBC.IfcLoader | null>(null)
  const clipperRef = useRef<OBC.Clipper | null>(null)
  const loadedRef = useRef<LoadedModel[]>([])
  const [ready, setReady] = useState(false)
  const [visibleDisciplines, setVisibleDisciplines] = useState<Set<string>>(new Set())
  const [status, setStatus] = useState('Initialisation du moteur 3D…')
  const [selected, setSelected] = useState<SelectedElement | null>(null)
  // Miroir de `selected.guid`, lu (pas juste écrit) par l'effet de sélection
  // croisée ci-dessous pour distinguer un focusGlobalId venu du GRAPHE d'un
  // simple écho du clic 3D qui vient d'ouvrir cette même popup (voir plus
  // bas -- sans cette distinction, cliquer un équipement en 3D se
  // fermait/rouvrait sa propre popup en boucle).
  const selectedGuidRef = useRef<string | null>(null)
  selectedGuidRef.current = selected?.guid ?? null
  const [navMode, setNavMode] = useState<'Orbit' | 'FirstPerson' | 'Plan'>('Orbit')
  const pressedKeysRef = useRef<Set<string>>(new Set())
  // Position de la souris, normalisée (-1..1) par rapport au centre de la
  // vue -- lue par la boucle de déplacement pour orienter la marche
  // uniquement pendant qu'on avance (voir l'effet de suivi souris plus bas).
  const mouseOffsetRef = useRef<{ x: number; y: number }>({ x: 0, y: 0 })

  // Sélecteur étage -> plan 2D cliquable affiché avant d'entrer en immersion,
  // demandé après deux retours utilisateur : (1) "je bouge la maquette, je ne
  // suis pas à l'intérieur" -- sans point de départ choisi, l'immersion
  // réactivait la caméra juste devant le bâtiment vu de l'extérieur ; (2) un
  // menu déroulant de noms de pièces ne permet pas de "cliquer sur la pièce
  // où on veut aller" -- il faut un vrai plan vu du dessus, cliquable.
  //
  // Étape "floor" : liste des étages (boutons, pas encore de vignette 2D par
  // étage -- ça demanderait un rendu séparé par étage, hors scope ici).
  // Étape "plan" : la vue 3D elle-même bascule en orthographique, cadrée sur
  // l'étage choisi vue du dessus (fitToBox) -- cliquer dedans réutilise le
  // même raycast que la popup d'info pour savoir sur quelle pièce on a
  // cliqué, puis téléporte à l'intérieur en immersion.
  // Étape "building" : une zone peut contenir PLUSIEURS bâtiments (une
  // maquette par discipline, chacune avec son propre GlobalId IFC -- ex:
  // AO+AP) -- sans ce choix préalable, la grille d'étages mélangeait les
  // niveaux de bâtiments différents dans une seule liste (bug remonté par
  // l'utilisateur). Insérée uniquement quand la zone compte >1 bâtiment ;
  // sinon on passe directement à 'floor' comme avant.
  type TeleportStep = 'choice' | 'building' | 'floor' | 'plan'
  type RoomWithBox = RoomOption & { box: THREE.Box3 }
  const [showTeleportPicker, setShowTeleportPicker] = useState(false)
  const [teleportStep, setTeleportStep] = useState<TeleportStep>('choice')
  const [teleportFloors, setTeleportFloors] = useState<FloorOption[]>([])
  const [teleportBuildings, setTeleportBuildings] = useState<BuildingOption[]>([])
  const [teleportBuildingId, setTeleportBuildingId] = useState<string | null>(null)
  const [teleportFloorsLoading, setTeleportFloorsLoading] = useState(false)
  const [planRoomsLoading, setPlanRoomsLoading] = useState(false)
  const [teleportError, setTeleportError] = useState('')
  const planRoomsRef = useRef<RoomWithBox[]>([])
  // Snapshot pris juste avant de passer en vue plan, pour restaurer la caméra
  // (position, cible, projection) si l'utilisateur annule sans choisir de pièce.
  const preTeleportCameraRef = useRef<{ position: THREE.Vector3; target: THREE.Vector3 } | null>(null)

  // Initialisation de la scène three.js : une seule fois par montage.
  useEffect(() => {
    if (!containerRef.current) return
    let cancelled = false

    const components = new OBC.Components()
    componentsRef.current = components

    const worlds = components.get(OBC.Worlds)
    const world = worlds.create<OBC.SimpleScene, OBC.OrthoPerspectiveCamera, OBC.SimpleRenderer>()
    world.scene = new OBC.SimpleScene(components)
    world.renderer = new OBC.SimpleRenderer(components, containerRef.current)
    // OrthoPerspectiveCamera (plutôt que SimpleCamera) : même API de base
    // (`.three`, `.controls`), mais ajoute les NavigationMode "Orbit" /
    // "FirstPerson" -- la marche façon jeu vidéo demandée pour approcher les
    // éléments sans avoir à re-zoomer/repositionner le centre de rotation.
    world.camera = new OBC.OrthoPerspectiveCamera(components)
    worldRef.current = world

    // Coupe horizontale utilisée pour isoler un étage dans le plan 2D de la
    // téléportation : sans ça, la vue de dessus superpose tous les étages
    // chargés (même silhouette quel que soit l'étage choisi -- bug remonté).
    const clipper = components.get(OBC.Clipper)
    clipper.enabled = false
    clipper.visible = false
    clipperRef.current = clipper

    components.init()
    world.scene.setup()
    world.camera.controls.setLookAt(15, 15, 15, 0, 0, 0)

    const ifcLoader = components.get(OBC.IfcLoader)
    ifcLoaderRef.current = ifcLoader

    const fragments = components.get(OBC.FragmentsManager)

    ;(async () => {
      try {
        // autoSetWasm résout par défaut le binaire WebAssembly web-ifc depuis
        // un CDN externe -- source d'échecs (mismatch de version entre le
        // binaire résolu et le code JS installé, ou CDN injoignable ; erreur
        // observée : "import object field 'a' is not an Object"). Le binaire
        // exact correspondant à la version de web-ifc installée est copié une
        // fois dans public/wasm/ (voir package.json) et servi par le front
        // lui-même -- cohérent avec le reste du projet, entièrement
        // autohébergé, sans dépendance réseau externe au chargement.
        await ifcLoader.setup({ autoSetWasm: false, wasm: { path: '/wasm/', absolute: false } })
        // OBC.FragmentsManager.getWorker() télécharge le worker fragments
        // depuis unpkg.com à chaque démarrage -- même classe de problème que
        // le binaire WASM ci-dessus (le raycast, qui passe par une route RPC
        // plus ancienne/stable, fonctionnait, mais Item.getGuid()/
        // getCategory()/getAttributes() échouaient avec "model[input.function]
        // is not a function" : le worker distant ne correspondait pas
        // exactement au code local). Le fichier exact de la version installée
        // (node_modules/@thatopen/fragments/dist/Worker/worker.mjs) est copié
        // une fois dans public/fragments-worker.mjs et servi par le front
        // lui-même, pour la même raison que le WASM -- à recopier si la
        // version de @thatopen/fragments change.
        const workerUrl = '/fragments-worker.mjs'
        if (cancelled) return
        fragments.init(workerUrl)
        world.camera.controls.addEventListener('rest', () => fragments.core.update(true))
        if (!cancelled) setReady(true)
      } catch (error) {
        if (!cancelled) {
          setStatus(error instanceof Error ? `Erreur d'initialisation 3D : ${error.message}` : 'Erreur d’initialisation 3D')
        }
      }
    })()

    return () => {
      cancelled = true
      for (const { model } of loadedRef.current) {
        try { model.dispose() } catch { /* ignore dispose errors on unmount */ }
      }
      loadedRef.current = []
      try { components.dispose() } catch { /* ignore dispose errors on unmount */ }
      componentsRef.current = null
      worldRef.current = null
      ifcLoaderRef.current = null
      clipperRef.current = null
    }
  }, [])

  // Chargement des sources IFC une fois le moteur prêt, ou quand la liste de
  // sources change (ex: changement de zone) : rechargement complet -- une
  // action peu fréquente (choix de zone), pas besoin d'un diff incrémental.
  useEffect(() => {
    if (!ready) return
    const world = worldRef.current
    const ifcLoader = ifcLoaderRef.current
    if (!world || !ifcLoader) return

    let cancelled = false
    setSelected(null)

    ;(async () => {
      for (const { model } of loadedRef.current) {
        try {
          world.scene.three.remove(model.object)
          model.dispose()
        } catch { /* ignore */ }
      }
      loadedRef.current = []

      if (sources.length === 0) {
        setStatus('Aucune maquette IFC ingérée pour cette zone.')
        setVisibleDisciplines(new Set())
        return
      }

      setStatus(`Chargement de ${sources.length} maquette(s)…`)
      const disciplinesLoaded: string[] = []

      for (const source of sources) {
        if (cancelled) return
        try {
          const bytes = await downloadModelBytes(source.storageUrl)
          if (cancelled) return
          const model = await ifcLoader.load(new Uint8Array(bytes), true, source.storageUrl)
          if (cancelled) {
            model.dispose()
            return
          }
          world.scene.three.add(model.object)
          model.useCamera(world.camera.three)
          loadedRef.current.push({ discipline: source.discipline, model })
          disciplinesLoaded.push(source.discipline)
        } catch (error) {
          console.error(`Échec du chargement de la maquette (${source.discipline}):`, error)
        }
      }

      if (cancelled) return
      setVisibleDisciplines(new Set(disciplinesLoaded))
      setStatus(`${disciplinesLoaded.length}/${sources.length} maquette(s) chargée(s).`)
    })()

    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, sources])

  const toggleDiscipline = (discipline: string, visible: boolean) => {
    setVisibleDisciplines((prev) => {
      const next = new Set(prev)
      if (visible) next.add(discipline)
      else next.delete(discipline)
      return next
    })
    for (const { discipline: d, model } of loadedRef.current) {
      if (d === discipline) model.object.visible = visible
    }
  }

  // Bascule Orbite <-> immersion première personne. Le regard (glisser la
  // souris) vient du NavigationMode natif de ThatOpenEngine ; le déplacement
  // (WASD/flèches, Espace/Maj pour monter-descendre) est piloté à la main
  // plus bas, la librairie ne le fournit pas. Pour ENTRER en immersion, on
  // propose d'abord un choix entre les deux modes (plan 2D ou manuel) --
  // les deux gardés volontairement pour le MVP, à montrer aux collègues --
  // pour EN SORTIR, retour direct.
  // Extrait pour être appelable aussi bien depuis le bouton que depuis la
  // touche Échap (voir handleKeyDown) -- affichée dans le petit tuto en
  // immersion, donc son fonctionnement réel doit correspondre exactement.
  // Généralisé pour revenir à l'Orbite depuis N'IMPORTE quel autre mode (pas
  // seulement Immersion) : Plan est en projection Orthographique, il faut
  // donc aussi la remettre en Perspective -- FirstPerson l'est déjà, ce
  // reset est alors un no-op sans effet indésirable pour ce cas-là.
  const enterOrbitMode = async () => {
    const world = worldRef.current
    if (!world) return
    if (world.camera.projection.current !== 'Perspective') {
      await world.camera.projection.set('Perspective')
    }
    world.camera.set('Orbit')
    setNavMode('Orbit')
    pressedKeysRef.current.clear()
  }

  // Point d'entrée réel de la téléportation. FirstPersonMode refuse de
  // s'activer si la projection n'est pas "Perspective" (elle l'est en
  // "Plan", utilisé pour le plan 2D juste avant) -- il faut donc la remettre
  // AVANT de positionner la caméra et de basculer le mode.
  const enterFirstPersonAt = async (position: THREE.Vector3) => {
    const world = worldRef.current
    const controls = world?.camera.controls
    if (!world || !controls) return
    // Retire la coupe du plan 2D : en immersion on doit voir la pièce
    // complète (murs, plafond), pas une tranche coupée au niveau du sol de
    // l'étage du dessus.
    clipperRef.current?.deleteAll()
    if (clipperRef.current) clipperRef.current.enabled = false
    if (world.camera.projection.current !== 'Perspective') {
      await world.camera.projection.set('Perspective')
    }
    await controls.setPosition(position.x, position.y, position.z, false)
    // Cible arbitraire à l'horizontale : juste pour donner une direction de
    // regard valide, FirstPersonMode recalcule de toute façon la distance.
    await controls.setTarget(position.x + 1, position.y, position.z, false)
    world.camera.set('FirstPerson')
    setNavMode('FirstPerson')
    pressedKeysRef.current.clear()
    setShowTeleportPicker(false)
    containerRef.current?.focus()
  }

  // Mode « manuel » : l'ancien comportement, gardé en option pour le MVP.
  // Contrairement à enterFirstPersonAt, ne repositionne pas la caméra --
  // l'immersion démarre là où l'utilisateur avait déjà zoomé/orienté la vue
  // orbite, à lui de s'être approché à la souris avant de cliquer.
  const enterFirstPersonManual = async () => {
    const world = worldRef.current
    if (!world) return
    clipperRef.current?.deleteAll()
    if (clipperRef.current) clipperRef.current.enabled = false
    if (world.camera.projection.current !== 'Perspective') {
      await world.camera.projection.set('Perspective')
    }
    world.camera.set('FirstPerson')
    setNavMode('FirstPerson')
    pressedKeysRef.current.clear()
    setShowTeleportPicker(false)
    containerRef.current?.focus()
  }

  // Popup affichée au clic sur « Immersion » : choix entre les deux modes.
  // Le snapshot de la caméra est pris ici, une seule fois, quel que soit le
  // mode choisi ensuite -- sert à restaurer la vue orbite si l'utilisateur
  // annule sans être entré en immersion.
  const openModeChoice = () => {
    const controls = worldRef.current?.camera.controls
    if (controls) {
      const position = new THREE.Vector3()
      const target = new THREE.Vector3()
      controls.getPosition(position)
      controls.getTarget(target)
      preTeleportCameraRef.current = { position, target }
    }
    setShowTeleportPicker(true)
    setTeleportStep('choice')
    setTeleportError('')
  }

  // Choisit l'étape suivante ('building' ou 'floor' directement) selon le
  // nombre de bâtiments distincts parmi les étages déjà résolus pour cette
  // zone -- appelé après le premier chargement ET à chaque réouverture (le
  // cache `teleportFloors` peut dater d'une zone/maquette différente).
  const routeToBuildingOrFloorStep = (floors: FloorOption[], buildings: BuildingOption[]) => {
    const buildingIds = Array.from(new Set(floors.map((f) => f.buildingId).filter((id): id is string => !!id)))
    if (buildingIds.length > 1) {
      setTeleportBuildings(buildings.filter((b) => buildingIds.includes(b.global_id)))
      setTeleportBuildingId(null)
      setTeleportStep('building')
    } else {
      setTeleportBuildingId(buildingIds[0] ?? null)
      setTeleportStep('floor')
    }
  }

  const openTeleportPicker = async () => {
    setTeleportError('')
    if (teleportFloors.length > 0) {
      routeToBuildingOrFloorStep(teleportFloors, teleportBuildings)
      return
    }
    setTeleportFloorsLoading(true)
    try {
      const [all, buildings] = await Promise.all([getFloors(zone), getBuildingsList(zone)])
      const zoneFloors = zone ? all.filter((f) => f.zone === zone) : all
      setTeleportFloors(zoneFloors)
      routeToBuildingOrFloorStep(zoneFloors, buildings)
    } catch (error) {
      setTeleportError(error instanceof Error ? error.message : 'Erreur de chargement des étages')
      setTeleportStep('floor')
    } finally {
      setTeleportFloorsLoading(false)
    }
  }

  const selectTeleportBuilding = (buildingId: string) => {
    setTeleportBuildingId(buildingId)
    setTeleportStep('floor')
  }

  // Repli identique à celui du clic dans le graphe (voir l'effet
  // `focusElement` ci-dessous) : si l'étage choisi n'a aucun IfcSpace, on ne
  // laisse pas l'utilisateur bloqué sur un message d'erreur -- on ferme le
  // sélecteur et on recadre au moins la caméra sur l'étage visé.
  const handleFloorCardClick = async (floorId: string) => {
    const opened = await selectTeleportFloor(floorId)
    if (!opened) {
      setShowTeleportPicker(false)
      await fitCameraToFloor(floorId)
    }
  }

  // Bascule la vue 3D elle-même en plan 2D vu du dessus (orthographique,
  // cadré sur l'étage) -- pas une vignette séparée, le même viewport que le
  // reste du visualiseur.
  const showFloorPlan = async (rooms: RoomWithBox[]) => {
    const world = worldRef.current
    if (!world || rooms.length === 0) return
    const unionBox = rooms[0].box.clone()
    for (const room of rooms.slice(1)) unionBox.union(room.box)

    if (world.camera.projection.current !== 'Orthographic') {
      await world.camera.projection.set('Orthographic')
    }
    world.camera.set('Plan')
    // phi (l'angle polaire) est l'angle de l'écart caméra→cible mesuré depuis
    // l'axe +Y (convention THREE.Spherical, vérifiée dans le code source de
    // camera-controls) : phi=0 place la caméra AU-DESSUS de la cible (vue du
    // dessus, ce qu'on veut) ; phi=Math.PI la place EN DESSOUS (vue du
    // dessous, en regardant vers le haut). J'avais mis Math.PI par erreur --
    // exactement le bug remonté ("j'ai la vue du dessous du bâtiment").
    await world.camera.controls.rotateTo(0, 0, false)
    await world.camera.controls.fitToBox(unionBox, false, {
      paddingTop: 2, paddingLeft: 2, paddingRight: 2, paddingBottom: 2,
    })

    // Coupe horizontale juste au-dessus du plafond de CET étage : sans elle,
    // la vue de dessus montre tous les étages superposés (même silhouette
    // pour chaque étage choisi, quel que soit celui sélectionné -- bug
    // remonté). Normale vers le bas : on garde tout ce qui est EN DESSOUS du
    // plan de coupe, donc cet étage (le sol de l'étage du dessus le masque).
    const clipper = clipperRef.current
    if (clipper) {
      clipper.deleteAll()
      // Coupe à hauteur d'appui (1,2 m au-dessus du sol), pas près du
      // plafond : convention standard d'un plan d'architecte -- les murs
      // vont presque toujours du sol au plafond, donc une coupe basse les
      // traverse de façon fiable quelle que soit la hauteur sous plafond
      // réelle de chaque pièce (qui varie d'un étage à l'autre). Couper près
      // du plafond (l'ancien calcul) ne traversait parfois que du mobilier,
      // ou rien du tout selon l'étage -- cause du plan illisible remonté.
      const center = unionBox.getCenter(new THREE.Vector3())
      const cutPoint = new THREE.Vector3(center.x, unionBox.min.y + 1.2, center.z)
      const planeId = clipper.createFromNormalAndCoplanarPoint(world, new THREE.Vector3(0, -1, 0), cutPoint)
      // clipper.enabled/.visible pilotent l'outil interactif (création à la
      // souris), pas le plan qu'on vient de créer par code -- c'est LE PLAN
      // lui-même qu'il faut activer, sans quoi rien n'est jamais découpé
      // (cause du bug "même plan quel que soit l'étage").
      const plane = clipper.list.get(planeId)
      if (plane) {
        plane.enabled = true
        plane.visible = false
      }
    }
  }

  // Recadre la caméra sur la boîte englobante d'UN élément (pas de
  // changement de mode caméra ni de projection) -- repli générique utilisé
  // à la fois par la sélection croisée du graphe et par la grille d'étages
  // manuelle quand le plan 2D est impossible (aucun IfcSpace dans le
  // fichier) : plutôt qu'un écran bloqué sur un message d'erreur, on montre
  // au moins l'étage visé en 3D.
  const fitCameraToGlobalId = async (globalId: string) => {
    const world = worldRef.current
    if (!world) return
    for (const { model } of loadedRef.current) {
      try {
        const [localId] = await model.getLocalIdsByGuids([globalId])
        if (localId === null || localId === undefined) continue
        const [box] = await model.getBoxes([localId])
        if (!box || box.isEmpty() || !Number.isFinite(box.min.x) || !Number.isFinite(box.max.x)) {
          // eslint-disable-next-line no-console
          console.warn('[Bim3D:focus] boîte englobante invalide/vide, recadrage annulé', globalId, box)
          return
        }
        // `false` (pas de transition animée) : un survol animé de longue
        // distance (ex: depuis une vue immersive proche d'un mur) peut
        // faire traverser des murs à la caméra en plein vol, ce qui
        // produit un écran noir transitoire (near-clipping) -- un saut
        // instantané, comme la téléportation du plan 2D d'étage, évite ça.
        await world.camera.controls.fitToBox(box, false)
        return
      } catch (error) {
        // eslint-disable-next-line no-console
        console.warn('[Bim3D:focus] échec sur un modèle, on essaie le suivant', error)
        continue
      }
    }
    // eslint-disable-next-line no-console
    console.warn('[Bim3D:focus] GlobalId introuvable dans les modèles chargés', globalId)
  }

  // Repli spécifique à un étage SANS pièce (aucun IfcSpace) : un
  // IfcBuildingStorey n'a généralement aucune géométrie propre, donc
  // `fitCameraToGlobalId(floorId)` ne trouve rien à cadrer. On recadre à la
  // place sur l'UNION des boîtes englobantes de son équipement (qui, lui, a
  // une géométrie réelle) -- même motif de résolution GUID->localId->box que
  // `selectTeleportFloor` pour les pièces. Repli sur `fitCameraToGlobalId`
  // en dernier recours si l'étage n'a vraiment aucun équipement localisable.
  const fitCameraToFloor = async (floorId: string) => {
    const world = worldRef.current
    if (!world) return
    try {
      const equipment = await getFloorEquipment(floorId, zone)
      if (equipment.length > 0) {
        const guids = equipment.map((e) => e.global_id)
        for (const { model } of loadedRef.current) {
          const localIds = await model.getLocalIdsByGuids(guids)
          const resolvedIds = localIds.filter((id): id is number => id !== null && id !== undefined)
          if (resolvedIds.length === 0) continue
          const boxes = (await model.getBoxes(resolvedIds)).filter(
            (b) => b && !b.isEmpty() && Number.isFinite(b.min.x) && Number.isFinite(b.max.x),
          )
          if (boxes.length === 0) continue
          const unionBox = boxes[0].clone()
          for (const b of boxes.slice(1)) unionBox.union(b)
          await world.camera.controls.fitToBox(unionBox, false)
          return
        }
      }
    } catch (error) {
      // eslint-disable-next-line no-console
      console.warn('[Bim3D:focus] échec de résolution de l\'équipement de l\'étage', error)
    }
    // Dernier recours : la géométrie du floor lui-même, s'il en a une.
    await fitCameraToGlobalId(floorId)
  }

  // Cadre TOUT le modèle chargé (toutes disciplines/étages confondus), pour
  // le bouton "Plan" du sélecteur de navigation -- contrairement à
  // showFloorPlan (plus bas) qui unionne les boîtes d'UN étage résolues via
  // GUID->localId->box (motif ThatOpen), ici on prend directement la boîte
  // englobante THREE.js de chaque `model.object` déjà chargé : pas besoin de
  // connaître la liste des GUID d'un étage en particulier, Box3.setFromObject
  // parcourt tous les enfants de lui-même.
  const fitCameraToAllModels = async () => {
    const world = worldRef.current
    if (!world || loadedRef.current.length === 0) return
    const unionBox = new THREE.Box3()
    for (const { model } of loadedRef.current) {
      unionBox.union(new THREE.Box3().setFromObject(model.object))
    }
    if (unionBox.isEmpty()) return
    await world.camera.controls.fitToBox(unionBox, false, {
      paddingTop: 2, paddingLeft: 2, paddingRight: 2, paddingBottom: 2,
    })
  }

  // Vue "Plan" générale (pas liée à un étage précis, contrairement à
  // showFloorPlan) : orthographique, vue du dessus, cadrée sur tout ce qui
  // est actuellement chargé. Pas de coupe horizontale ajoutée ici (on veut
  // la silhouette de l'ensemble, pas isoler un seul étage) -- l'utilisateur
  // qui veut un plan d'étage précis avec coupe passe par "Immersion" ->
  // sélection d'étage, comme avant.
  const enterPlanOverview = async () => {
    const world = worldRef.current
    if (!world) return
    clipperRef.current?.deleteAll()
    if (clipperRef.current) clipperRef.current.enabled = false
    if (world.camera.projection.current !== 'Orthographic') {
      await world.camera.projection.set('Orthographic')
    }
    world.camera.set('Plan')
    // Voir le commentaire détaillé sur showFloorPlan : phi=0 place la
    // caméra au-dessus de la cible (vue du dessus voulue).
    await world.camera.controls.rotateTo(0, 0, false)
    await fitCameraToAllModels()
    setNavMode('Plan')
    pressedKeysRef.current.clear()
  }

  const selectTeleportFloor = async (floorId: string): Promise<boolean> => {
    setTeleportError('')
    setPlanRoomsLoading(true)
    try {
      const rooms = await getFloorRooms(floorId, zone)
      if (rooms.length === 0) {
        // Vide explicitement l'état du plan précédent : sans ça, la vue 3D
        // gardait le découpage du dernier étage affiché avec succès, donnant
        // l'impression trompeuse que "c'est toujours le même plan" alors
        // qu'en réalité aucun plan n'était (re)généré pour cet étage.
        planRoomsRef.current = []
        clipperRef.current?.deleteAll()
        setTeleportStep('floor')
        setTeleportError('Aucune pièce trouvée pour cet étage (le fichier IFC ne contient aucun IfcSpace).')
        return false
      }
      // Une pièce n'a pas de position 3D dans le graphe (ArcadeDB ne stocke
      // que du relationnel) -- on la retrouve par GUID dans la maquette IFC
      // déjà chargée pour obtenir sa boîte englobante réelle.
      const guids = rooms.map((r) => r.global_id)
      let resolved: RoomWithBox[] = []
      for (const { model } of loadedRef.current) {
        const localIds = await model.getLocalIdsByGuids(guids)
        const pairs = rooms
          .map((room, i) => ({ room, localId: localIds[i] }))
          .filter((p): p is { room: RoomOption; localId: number } => p.localId !== null && p.localId !== undefined)
        if (pairs.length === 0) continue
        const boxes = await model.getBoxes(pairs.map((p) => p.localId))
        resolved = pairs.map((p, i) => ({ ...p.room, box: boxes[i] })).filter((r) => !!r.box)
        if (resolved.length > 0) break
      }
      if (resolved.length === 0) {
        setTeleportError('Impossible de localiser les pièces de cet étage dans la maquette 3D chargée.')
        return false
      }
      planRoomsRef.current = resolved
      await showFloorPlan(resolved)
      setTeleportStep('plan')
      return true
    } catch (error) {
      setTeleportError(error instanceof Error ? error.message : 'Erreur de chargement des pièces')
      return false
    } finally {
      setPlanRoomsLoading(false)
    }
  }

  // Clic dans le plan 2D : identifie la pièce sous le point cliqué (parmi
  // celles résolues pour cet étage) via son emprise horizontale, puis entre
  // en immersion au sol de CETTE pièce précisément, à l'endroit cliqué.
  const handlePlanClick = async (event: React.MouseEvent<HTMLDivElement>) => {
    // eslint-disable-next-line no-console
    console.groupCollapsed('[Bim3D:plan] clic sur le plan 2D')
    try {
      const world = worldRef.current
      if (!world?.renderer) {
        // eslint-disable-next-line no-console
        console.warn('[Bim3D:plan] ARRÊT -- moteur 3D pas prêt')
        return
      }
      const canvas = world.renderer.three.domElement
      const mouse = new THREE.Vector2(event.clientX, event.clientY)

      let hitPoint: THREE.Vector3 | null = null
      for (const { discipline, model } of loadedRef.current) {
        if (!visibleDisciplines.has(discipline)) continue
        const result = await model.raycast({ camera: world.camera.three, mouse, dom: canvas })
        // eslint-disable-next-line no-console
        console.log(`[Bim3D:plan]   ${discipline} ->`, result ? { touche: true, point: result.point } : { touche: false })
        if (result) {
          hitPoint = result.point
          break
        }
      }
      if (!hitPoint) {
        // eslint-disable-next-line no-console
        console.warn('[Bim3D:plan] ARRÊT -- rayon sans intersection à cet endroit du plan')
        setTeleportError('Cliquez directement sur une pièce du plan.')
        return
      }

      const point = hitPoint
      const room = planRoomsRef.current.find((r) =>
        point.x >= r.box.min.x && point.x <= r.box.max.x &&
        point.z >= r.box.min.z && point.z <= r.box.max.z,
      )
      // eslint-disable-next-line no-console
      console.log('[Bim3D:plan] pièces connues pour cet étage', planRoomsRef.current.map((r) => r.name || r.global_id))
      if (!room) {
        // eslint-disable-next-line no-console
        console.warn('[Bim3D:plan] ARRÊT -- point touché hors de toute boîte englobante de pièce connue', { point })
        setTeleportError('Cliquez directement sur une pièce du plan.')
        return
      }
      // eslint-disable-next-line no-console
      console.log('[Bim3D:plan] pièce identifiée, téléportation vers son centre', { piece: room.name || room.global_id })
      setTeleportError('')
      // Centre de la pièce, pas le point cliqué exact : cliquer près d'un
      // bord aurait pu faire atterrir dans un mur. Le clic ne sert donc
      // qu'à désigner QUELLE pièce, la marche libre (ZQSD) prend ensuite le
      // relais pour se déplacer à l'intérieur.
      const roomCenter = room.box.getCenter(new THREE.Vector3())
      await enterFirstPersonAt(new THREE.Vector3(roomCenter.x, room.box.min.y + 1.6, roomCenter.z))
    } catch (error) {
      // eslint-disable-next-line no-console
      console.error('[Bim3D:plan] ÉCHEC INATTENDU', error)
    } finally {
      // eslint-disable-next-line no-console
      console.groupEnd()
    }
  }

  const closeTeleportPicker = async () => {
    const world = worldRef.current
    const snapshot = preTeleportCameraRef.current
    clipperRef.current?.deleteAll()
    if (clipperRef.current) clipperRef.current.enabled = false
    if (world) {
      if (world.camera.projection.current !== 'Perspective') {
        await world.camera.projection.set('Perspective')
      }
      world.camera.set('Orbit')
      if (snapshot) {
        await world.camera.controls.setLookAt(
          snapshot.position.x, snapshot.position.y, snapshot.position.z,
          snapshot.target.x, snapshot.target.y, snapshot.target.z,
          false,
        )
      }
    }
    setShowTeleportPicker(false)
    setTeleportStep('choice')
    setTeleportError('')
  }

  // Suivi de la position de la souris, en continu, tant qu'on est en
  // immersion -- pas de capture (Pointer Lock) : demandé pour que la souris
  // reste libre (utilisable normalement) quand on n'avance pas, et ne serve
  // à orienter la marche que lorsqu'on avance réellement (ci-dessous).
  useEffect(() => {
    if (navMode !== 'FirstPerson') return
    const container = containerRef.current
    if (!container) return

    const handleMouseMove = (event: MouseEvent) => {
      const rect = container.getBoundingClientRect()
      const cx = rect.left + rect.width / 2
      const cy = rect.top + rect.height / 2
      mouseOffsetRef.current = {
        x: Math.max(-1, Math.min(1, (event.clientX - cx) / (rect.width / 2))),
        y: Math.max(-1, Math.min(1, (event.clientY - cy) / (rect.height / 2))),
      }
    }
    container.addEventListener('mousemove', handleMouseMove)
    return () => {
      container.removeEventListener('mousemove', handleMouseMove)
      mouseOffsetRef.current = { x: 0, y: 0 }
    }
  }, [navMode])

  // Boucle de déplacement, active uniquement en immersion. `forward` avance
  // vers la direction du regard, `truck` déplace latéralement, `elevate`
  // monte/descend -- les trois méthodes de camera-controls prévues pour ce
  // type de navigation libre. En avançant (et seulement en avançant), la
  // position de la souris à l'écran oriente progressivement la marche --
  // demandé pour rester simple : pas besoin de capturer la souris, elle ne
  // pilote la direction que quand on avance vraiment, sinon elle reste
  // libre pour cliquer ailleurs sans faire tourner la vue.
  useEffect(() => {
    if (navMode !== 'FirstPerson') return
    const controls = worldRef.current?.camera.controls
    if (!controls) return

    const TURN_SPEED = 2.2 // rad/s à l'écart maximal (souris tout au bord de la vue)
    let raf = 0
    let last = performance.now()

    const tick = (now: number) => {
      const dt = (now - last) / 1000
      last = now
      const keys = pressedKeysRef.current
      const fwd = (keys.has('KeyW') || keys.has('ArrowUp') ? 1 : 0) - (keys.has('KeyS') || keys.has('ArrowDown') ? 1 : 0)
      const strafe = (keys.has('KeyD') || keys.has('ArrowRight') ? 1 : 0) - (keys.has('KeyA') || keys.has('ArrowLeft') ? 1 : 0)
      const up = (keys.has('Space') ? 1 : 0) - (keys.has('ShiftLeft') || keys.has('ShiftRight') ? 1 : 0)
      if (fwd > 0) {
        const { x: offX, y: offY } = mouseOffsetRef.current
        // Même convention que partout ailleurs dans ce fichier : souris à
        // droite/en bas -> angles négatifs (voir enterFirstPersonAt et
        // showFloorPlan pour la même logique de signe).
        if (offX !== 0 || offY !== 0) controls.rotate(-offX * TURN_SPEED * dt, -offY * TURN_SPEED * dt, false)
      }
      if (fwd) controls.forward(fwd * MOVE_SPEED * dt, false)
      if (strafe) controls.truck(strafe * MOVE_SPEED * dt, 0, false)
      if (up) controls.elevate(up * MOVE_SPEED * dt, false)
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [navMode])

  // Sélection croisée (Mode Duo) : un clic dans le GRAPHE fournit un
  // GlobalId, ici on le résout en élément 3D et on recadre la caméra dessus
  // -- sans changer de mode caméra (Orbit/FirstPerson) ni de projection,
  // juste fitToBox comme le bouton "Recentrer", mais sur la boîte englobante
  // du SEUL élément visé plutôt que la scène entière.
  useEffect(() => {
    if (!focusElement) return
    // Écho du clic 3D qui vient d'ouvrir sa propre popup (onElementSelect
    // fait remonter le même GlobalId au parent, qui le renvoie ici tel
    // quel) -- ne RIEN faire : la popup est déjà ouverte et la caméra déjà
    // sur cet élément, retraiter fermerait la popup qu'on vient d'ouvrir.
    if (focusElement.id === selectedGuidRef.current) return
    let cancelled = false
    const { id: focusGlobalId, type: focusType } = focusElement

    const focus = async () => {
      const world = worldRef.current
      if (!world) return
      // Ferme une éventuelle popup de fiche IFC laissée par un clic 3D
      // précédent -- elle décrirait un élément différent de celui qu'on
      // recadre maintenant depuis le graphe.
      setSelected(null)

      // Un Floor cliqué dans le graphe donne le MÊME découpage en plan 2D
      // que le flux "Immersion -> Choisir un étage" (demandé -- sinon
      // cliquer "Level 1"/"Roof" ne faisait qu'un recadrage caméra
      // générique, moins utile que la vraie vue de plan). `teleportStep`
      // mis à 'floor' avant l'appel : si selectTeleportFloor (async) prend
      // un instant, l'écran intermédiaire visible est la grille des étages
      // (avec son propre indicateur de chargement), pas l'écran de choix
      // Orbite/Première personne qui n'a pas de sens ici.
      //
      // Repli sur le recadrage caméra générique (comme tout autre type de
      // nœud) si le plan 2D échoue -- typiquement une maquette industrielle
      // sans aucun IfcSpace (0 pièce) : sans ce repli, le clic n'affichait
      // qu'une modale bloquée sur un message d'erreur, sans jamais recadrer
      // sur l'étage lui-même.
      if (focusType === 'Floor') {
        setShowTeleportPicker(true)
        setTeleportStep('floor')
        // Résout le bâtiment de CET étage pour scoper la grille sur le même
        // bâtiment -- sans ça, un `teleportBuildingId` mis en cache par un
        // clic manuel précédent (bouton Immersion) pouvait rester sur un
        // AUTRE bâtiment de la même zone, affichant des étages qui n'ont
        // rien à voir avec celui cliqué dans le graphe (bug remonté par
        // l'utilisateur : niveaux d'un autre bâtiment mélangés à la liste).
        try {
          let floors = teleportFloors
          if (floors.length === 0) {
            const all = await getFloors(zone)
            floors = zone ? all.filter((f) => f.zone === zone) : all
            if (!cancelled) setTeleportFloors(floors)
          }
          if (!cancelled) {
            setTeleportBuildingId(floors.find((f) => f.global_id === focusGlobalId)?.buildingId ?? null)
          }
        } catch (error) {
          // eslint-disable-next-line no-console
          console.warn('[Bim3D:focus] résolution du bâtiment de l\'étage échouée', error)
        }
        const opened = await selectTeleportFloor(focusGlobalId)
        if (cancelled) return
        if (!opened) {
          setShowTeleportPicker(false)
          await fitCameraToFloor(focusGlobalId)
        }
        return
      }

      await fitCameraToGlobalId(focusGlobalId)
    }

    focus()
    return () => {
      cancelled = true
    }
  }, [focusElement])

  // "Coloriser en 3D" (Mode Duo, TypologyPanel.tsx) : peint la géométrie des
  // pièces d'un groupe avec la couleur choisie par l'utilisateur (pas de
  // couleur imposée -- chaque typologie a son propre sélecteur, voir
  // TypologyPanel). `setColor`/`resetColor` existent nativement sur
  // FragmentsModel, aucune dépendance supplémentaire. Le ref garde la trace
  // de CE QUI a été peint pour pouvoir l'effacer avant d'appliquer un
  // nouveau groupe/couleur (sinon une ancienne coloration resterait visible
  // en plus de la nouvelle).
  const coloredRef = useRef<{ model: FragmentsModel; localIds: number[] }[]>([])
  useEffect(() => {
    let cancelled = false

    const applyColor = async () => {
      // Efface systématiquement la coloration précédente avant d'appliquer
      // la nouvelle (ou de ne rien appliquer, si colorGroup est vide/null).
      const previous = coloredRef.current
      coloredRef.current = []
      for (const { model, localIds } of previous) {
        try {
          await model.resetColor(localIds)
        } catch {
          // modèle déchargé entre-temps -- rien à nettoyer
        }
      }

      if (!colorGroup || colorGroup.ids.length === 0) return
      const threeColor = new THREE.Color(colorGroup.color)

      for (const { model } of loadedRef.current) {
        try {
          const localIds = (await model.getLocalIdsByGuids(colorGroup.ids)).filter(
            (id): id is number => id !== null && id !== undefined,
          )
          if (cancelled || localIds.length === 0) continue
          await model.setColor(localIds, threeColor)
          coloredRef.current.push({ model, localIds })
        } catch (error) {
          // eslint-disable-next-line no-console
          console.warn('[Bim3D:color] échec sur un modèle, on essaie le suivant', error)
        }
      }
    }

    applyColor()
    return () => {
      cancelled = true
    }
  }, [colorGroup])

  // Même hook que la popup temps réel du graphe (EquipmentTelemetryPopup) --
  // ici la clé de recherche est le GlobalId lu en 3D (item.getGuid())
  // plutôt que l'id de nœud Cytoscape. La plupart des clics 3D ne tombent
  // PAS sur un Equipment (murs, dalles, portes...), d'où le filtre sur
  // 'Inconnu' : pas la peine de tenter un fetch/WebSocket voués à un 404.
  const { metrics: selectedMetrics, error: selectedMetricsError } = useEquipmentTelemetry(
    selected && selected.guid !== 'Inconnu' ? selected.guid : null,
    zone,
  )

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    // Échap ramène à l'Orbite depuis Immersion OU Plan -- WASD/flèches
    // (ci-dessous) restent spécifiques à Immersion, Plan n'a pas de
    // déplacement au clavier.
    if (event.code === 'Escape' && navMode !== 'Orbit') {
      enterOrbitMode()
      return
    }
    if (navMode !== 'FirstPerson') return
    if (!MOVEMENT_KEYS.has(event.code)) return
    event.preventDefault()
    pressedKeysRef.current.add(event.code)
  }

  const handleKeyUp = (event: React.KeyboardEvent<HTMLDivElement>) => {
    pressedKeysRef.current.delete(event.code)
  }

  // POC (Paul, point 7 de la roadmap) : clic sur un élément de la maquette ->
  // récupère sa référence IFC (GlobalId) et l'affiche dans une popup. Pas
  // encore de données temps réel ici -- seulement la collecte de référence.
  //
  // Points de surveillance/audit ajoutés à la demande, pour diagnostiquer
  // précisément où ça bloque sans deviner : console.log/warn/error (PAS
  // console.debug, masqué par défaut dans l'onglet Console de F12 tant que
  // le niveau "Verbose" n'est pas coché) à chaque étape du parcours décrit
  // dans Architecture-Technique.html (clic -> raycast (worker) -> résolution
  // GlobalId/Type/Nom (worker) -> popup). Ouvrir F12 > Console avant de
  // cliquer sur un mur pour voir le détail groupé de chaque tentative.
  const handleElementClick = async (event: React.MouseEvent<HTMLDivElement>) => {
    // eslint-disable-next-line no-console
    console.groupCollapsed('[Bim3D:clic] nouveau clic sur la maquette 3D')
    try {
      const world = worldRef.current
      const container = containerRef.current
      if (!world?.renderer || !container) {
        // eslint-disable-next-line no-console
        console.warn('[Bim3D:clic] ARRÊT -- moteur 3D pas prêt', { world: !!world, renderer: !!world?.renderer, container: !!container })
        return
      }

      const canvas = world.renderer.three.domElement
      // FragmentsModel.raycast() convertit lui-même en NDC en interne à partir
      // de `dom.getBoundingClientRect()` -- il attend donc des coordonnées
      // écran brutes (comme event.clientX/Y), pas des coordonnées déjà
      // normalisées. Les passer déjà normalisées ici (bug initial) envoyait un
      // point hors de l'écran et ne touchait jamais rien, silencieusement.
      const mouse = new THREE.Vector2(event.clientX, event.clientY)
      // eslint-disable-next-line no-console
      console.log('[Bim3D:clic] 1/4 position du clic', { clientX: event.clientX, clientY: event.clientY })

      const candidates = loadedRef.current.filter(({ discipline }) => visibleDisciplines.has(discipline))
      // eslint-disable-next-line no-console
      console.log('[Bim3D:clic] modèles candidats (chargés + case visible cochée)', {
        total_charges: loadedRef.current.length,
        disciplines_chargees: loadedRef.current.map((c) => c.discipline),
        total_visibles: candidates.length,
        disciplines_visibles: candidates.map((c) => c.discipline),
      })
      if (candidates.length === 0) {
        // eslint-disable-next-line no-console
        console.warn('[Bim3D:clic] ARRÊT -- aucun modèle chargé et visible : rien à toucher. Vérifier la case à cocher de la discipline en haut à gauche du viewer.')
        return
      }

      // eslint-disable-next-line no-console
      console.log('[Bim3D:clic] 2/4 lancer de rayon (raycast) sur chaque modèle candidat, dans le worker fragments-worker.mjs...')
      let closest: RaycastResult | null = null
      for (const { discipline, model } of candidates) {
        try {
          const result = await model.raycast({ camera: world.camera.three, mouse, dom: canvas })
          // eslint-disable-next-line no-console
          console.log(`[Bim3D:clic]   ${discipline} ->`, result ? { touche: true, localId: result.localId, distance: result.distance } : { touche: false })
          if (result && (!closest || result.distance < closest.distance)) closest = result
        } catch (raycastError) {
          // eslint-disable-next-line no-console
          console.error(`[Bim3D:clic]   ${discipline} -> ÉCHEC du raycast`, raycastError)
        }
      }

      if (!closest) {
        // eslint-disable-next-line no-console
        console.warn('[Bim3D:clic] ARRÊT -- aucun élément touché par le rayon à cet endroit précis (clic dans le vide, ou hors géométrie visible).')
        setSelected(null)
        return
      }
      // eslint-disable-next-line no-console
      console.log('[Bim3D:clic] élément retenu (le plus proche)', { localId: closest.localId, distance: closest.distance })

      // eslint-disable-next-line no-console
      console.log("[Bim3D:clic] 3/4 résolution de l'identité IFC (GlobalId/Type/Nom), toujours dans le worker...")
      const item = closest.fragments.getItem(closest.localId)

      let guid: string | null = null
      try {
        guid = await item.getGuid()
        // eslint-disable-next-line no-console
        console.log('[Bim3D:clic]   getGuid() ->', guid)
      } catch (guidError) {
        // eslint-disable-next-line no-console
        console.error('[Bim3D:clic]   ÉCHEC item.getGuid()', guidError)
      }

      let category: string | null = null
      try {
        category = await item.getCategory()
        // eslint-disable-next-line no-console
        console.log('[Bim3D:clic]   getCategory() ->', category)
      } catch (categoryError) {
        // eslint-disable-next-line no-console
        console.error('[Bim3D:clic]   ÉCHEC item.getCategory()', categoryError)
      }

      let attributes: Awaited<ReturnType<typeof item.getAttributes>> | null = null
      try {
        attributes = await item.getAttributes()
        // eslint-disable-next-line no-console
        console.log('[Bim3D:clic]   getAttributes() -> clés disponibles', attributes ? Array.from(attributes.keys()) : null)
      } catch (attributesError) {
        // eslint-disable-next-line no-console
        console.error('[Bim3D:clic]   ÉCHEC item.getAttributes()', attributesError)
      }
      const name = (attributes?.get('Name') as { value?: unknown } | undefined)?.value

      const containerRect = container.getBoundingClientRect()
      const selection = {
        guid: guid || 'Inconnu',
        category: category || '',
        name: typeof name === 'string' ? name : '',
        x: event.clientX - containerRect.left,
        y: event.clientY - containerRect.top,
      }
      // eslint-disable-next-line no-console
      console.log('[Bim3D:clic] 4/4 affichage de la popup', selection)
      setSelected(selection)
      // Sélection croisée (Mode Duo) : un GlobalId "Inconnu" (getGuid() en
      // échec) n'a pas d'équivalent identifiable côté graphe, ne rien remonter.
      if (guid) onElementSelect?.(guid, category || '')
    } catch (error) {
      // eslint-disable-next-line no-console
      console.error('[Bim3D:clic] ÉCHEC INATTENDU (hors des points surveillés ci-dessus)', error)
    } finally {
      // eslint-disable-next-line no-console
      console.groupEnd()
    }
  }

  const disciplines = Array.from(new Set(sources.map((s) => s.discipline)))

  return (
    <div
      style={{
        width: '100%',
        height,
        border: '1px solid var(--gray-200, #334155)',
        borderRadius: 12,
        overflow: 'hidden',
        background: '#111827',
        position: 'relative',
        display: 'flex',
      }}
    >
      <div
        ref={containerRef}
        onClick={showTeleportPicker && teleportStep === 'plan' ? handlePlanClick : handleElementClick}
        onKeyDown={handleKeyDown}
        onKeyUp={handleKeyUp}
        tabIndex={0}
        style={{ flex: 1, minWidth: 0, outline: 'none' }}
      />

      {navMode === 'FirstPerson' && (
        <div
          style={{
            position: 'absolute', bottom: 14, left: 14, zIndex: 4,
            background: 'rgba(17, 24, 39, 0.85)', border: '1px solid #334155', borderRadius: 8,
            padding: '10px 12px', fontSize: 12, color: '#e2e8f0', lineHeight: 1.7, minWidth: 168,
          }}
        >
          <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: 0.04, textTransform: 'uppercase', color: '#94a3b8', marginBottom: 4 }}>
            Déplacement
          </div>
          {[
            ['Z / W', 'Avancer'],
            ['S', 'Reculer'],
            ['Q / A', 'Gauche'],
            ['D', 'Droite'],
            ['Espace', 'Monter'],
            ['Maj', 'Descendre'],
          ].map(([key, label]) => (
            <div key={key} style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
              <span style={{ color: '#94a3b8' }}>{label}</span>
              <span style={{ fontFamily: 'monospace' }}>{key}</span>
            </div>
          ))}
          <div style={{ borderTop: '1px solid #334155', margin: '6px 0' }} />
          <div>Souris en avançant — diriger la marche</div>
          <div style={{ marginTop: 4, color: '#93c5fd' }}>Échap — quitter l'immersion</div>
        </div>
      )}

      <div
        style={{
          position: 'absolute',
          top: 10,
          left: 10,
          display: 'flex',
          flexDirection: 'column',
          gap: 4,
          background: 'rgba(17, 24, 39, 0.85)',
          borderRadius: 8,
          padding: '10px 12px',
          maxWidth: 220,
        }}
      >
        <span style={{ fontSize: 11, color: '#94a3b8', marginBottom: 2 }}>{status}</span>
        {disciplines.map((discipline) => (
          <label key={discipline} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5, color: '#e2e8f0' }}>
            <input
              type="checkbox"
              checked={visibleDisciplines.has(discipline)}
              onChange={(event) => toggleDiscipline(discipline, event.target.checked)}
            />
            {discipline}
          </label>
        ))}
      </div>

      {/* Sélecteur de mode de navigation -- Orbite (rotation libre autour du
          modèle), Immersion/FirstPerson (déplacement à hauteur d'humain,
          ZQSD/flèches, ouvre d'abord le choix de pièce), Plan (vue
          orthographique du dessus, proportions exactes). Chaque bouton
          bascule directement dans son mode, sauf Immersion qui ouvre le
          sélecteur de pièce (comportement déjà existant, conservé car utile
          pour se téléporter précisément). */}
      <div style={{ position: 'absolute', top: 10, right: 52, display: 'flex', gap: 4 }}>
        <button
          type="button"
          title="Orbite -- rotation libre autour du modèle"
          onClick={enterOrbitMode}
          style={{
            height: 32, padding: '0 10px', borderRadius: 6,
            border: '1px solid #334155', background: navMode === 'Orbit' ? '#2563eb' : '#1f2937',
            color: '#e2e8f0', cursor: 'pointer', fontSize: 12.5, whiteSpace: 'nowrap',
          }}
        >
          🖱 Orbite
        </button>
        <button
          type="button"
          title="Immersion -- choisir une pièce et se déplacer à hauteur d'humain (ZQSD/flèches, souris pour regarder)"
          onClick={openModeChoice}
          style={{
            height: 32, padding: '0 10px', borderRadius: 6,
            border: '1px solid #334155', background: navMode === 'FirstPerson' ? '#2563eb' : '#1f2937',
            color: '#e2e8f0', cursor: 'pointer', fontSize: 12.5, whiteSpace: 'nowrap',
          }}
        >
          🎮 Immersion
        </button>
        <button
          type="button"
          title="Plan -- vue orthographique du dessus, proportions exactes"
          onClick={enterPlanOverview}
          style={{
            height: 32, padding: '0 10px', borderRadius: 6,
            border: '1px solid #334155', background: navMode === 'Plan' ? '#2563eb' : '#1f2937',
            color: '#e2e8f0', cursor: 'pointer', fontSize: 12.5, whiteSpace: 'nowrap',
          }}
        >
          📐 Plan
        </button>
      </div>

      <button
        type="button"
        title="Recentrer"
        onClick={() => worldRef.current?.camera.controls.setLookAt(15, 15, 15, 0, 0, 0, true)}
        style={{
          position: 'absolute', top: 10, right: 10, width: 32, height: 32, borderRadius: 6,
          border: '1px solid #334155', background: '#1f2937', color: '#e2e8f0', cursor: 'pointer', fontSize: 16,
        }}
      >
        ⤢
      </button>

      {selected && (
        <div
          style={{
            position: 'absolute',
            left: selected.x + 12,
            top: selected.y + 12,
            background: 'rgba(17, 24, 39, 0.95)',
            border: '1px solid #334155',
            borderRadius: 8,
            padding: '10px 12px',
            width: 260,
            boxSizing: 'border-box',
            maxHeight: '70vh',
            overflowY: 'auto',
            color: '#e2e8f0',
            fontSize: 12.5,
            zIndex: 5,
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, marginBottom: 6 }}>
            <strong style={{ fontSize: 13 }} title={selected.name}>
              {selected.name ? truncateLabel(selected.name) : 'Élément'}
            </strong>
            <button
              type="button"
              onClick={() => setSelected(null)}
              style={{ background: 'none', border: 'none', color: '#94a3b8', cursor: 'pointer', fontSize: 14, lineHeight: 1, padding: 0 }}
            >
              ✕
            </button>
          </div>
          <div style={{ color: '#94a3b8' }}>Type IFC</div>
          <div style={{ marginBottom: 6 }}>{selected.category || '—'}</div>

          <div style={{ borderTop: '1px solid #334155', paddingTop: 6, color: '#94a3b8' }}>
            Télémétrie live
          </div>
          {selectedMetricsError && (
            <div style={{ color: '#f87171', marginTop: 2 }}>{selectedMetricsError}</div>
          )}
          {!selectedMetricsError && selectedMetrics === null && (
            <div style={{ color: '#94a3b8', marginTop: 2 }}>Chargement…</div>
          )}
          {!selectedMetricsError && selectedMetrics !== null && selectedMetrics.length === 0 && (
            <div style={{ color: '#94a3b8', marginTop: 2 }}>Aucune télémétrie pour cet élément.</div>
          )}
          {!selectedMetricsError && selectedMetrics !== null && selectedMetrics.length > 0 && (
            <div style={{ marginTop: 2, display: 'flex', flexDirection: 'column', gap: 8 }}>
              {selectedMetrics.map((m) => (
                <div key={m.metric} style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
                    <span>{m.metric}</span>
                    <span>{formatMetricValue(m)}</span>
                  </div>
                  <MetricHistoryPanel metric={m} />
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {showTeleportPicker && teleportStep === 'choice' && (
        <div
          style={{
            position: 'absolute', inset: 0, background: 'rgba(11, 17, 32, 0.88)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10,
          }}
        >
          <div
            style={{
              background: '#1f2937', border: '1px solid #334155', borderRadius: 10,
              padding: 18, width: 320, color: '#e2e8f0',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
              <strong style={{ fontSize: 14 }}>Comment entrer en immersion ?</strong>
              <button
                type="button"
                onClick={() => setShowTeleportPicker(false)}
                style={{ background: 'none', border: 'none', color: '#94a3b8', cursor: 'pointer', fontSize: 16, lineHeight: 1 }}
              >
                ✕
              </button>
            </div>
            <p style={{ fontSize: 12, color: '#94a3b8', marginTop: 4, marginBottom: 14 }}>
              Deux modes disponibles pour le MVP.
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <button
                type="button"
                onClick={openTeleportPicker}
                style={{
                  textAlign: 'left', padding: '10px 12px', borderRadius: 8, border: '1px solid #334155',
                  background: '#111827', color: '#e2e8f0', cursor: 'pointer',
                }}
              >
                <div style={{ fontSize: 13.5, fontWeight: 600, marginBottom: 2 }}>🗺️ Plan 2D</div>
                <div style={{ fontSize: 12, color: '#94a3b8' }}>Choisir un étage puis une pièce sur un plan vu du dessus.</div>
              </button>
              <button
                type="button"
                onClick={enterFirstPersonManual}
                style={{
                  textAlign: 'left', padding: '10px 12px', borderRadius: 8, border: '1px solid #334155',
                  background: '#111827', color: '#e2e8f0', cursor: 'pointer',
                }}
              >
                <div style={{ fontSize: 13.5, fontWeight: 600, marginBottom: 2 }}>🖱 Manuel</div>
                <div style={{ fontSize: 12, color: '#94a3b8' }}>Se rapprocher soi-même en vue orbite avant d'entrer, comme avant.</div>
                <div style={{ fontSize: 11, color: '#64748b', marginTop: 4 }}>Une fois dedans : cliquez dans la vue pour capturer la souris (regard libre façon FPS), Échap pour la relâcher.</div>
              </button>
            </div>
          </div>
        </div>
      )}

      {showTeleportPicker && teleportStep === 'building' && (
        <div
          style={{
            position: 'absolute', inset: 0, background: 'rgba(11, 17, 32, 0.88)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10,
          }}
        >
          <div
            style={{
              background: '#1f2937', border: '1px solid #334155', borderRadius: 10,
              padding: 18, width: 340, maxHeight: '80%', overflowY: 'auto', color: '#e2e8f0',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
              <strong style={{ fontSize: 14 }}>Choisir un bâtiment</strong>
              <button
                type="button"
                onClick={closeTeleportPicker}
                style={{ background: 'none', border: 'none', color: '#94a3b8', cursor: 'pointer', fontSize: 16, lineHeight: 1 }}
              >
                ✕
              </button>
            </div>
            <p style={{ fontSize: 12, color: '#94a3b8', marginTop: 4, marginBottom: 14 }}>
              Cette zone contient plusieurs bâtiments (une maquette par discipline) : choisissez-en un
              pour voir uniquement ses propres étages.
            </p>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              {teleportBuildings.map((building) => (
                <button
                  key={building.global_id}
                  type="button"
                  onClick={() => selectTeleportBuilding(building.global_id)}
                  style={{
                    display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                    gap: 6, padding: '14px 8px', borderRadius: 8, border: '1px solid #334155',
                    background: '#111827', color: '#e2e8f0', cursor: 'pointer', fontSize: 12.5,
                  }}
                >
                  <span style={{ fontSize: 20 }}>🏗️</span>
                  {building.name || building.global_id}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {showTeleportPicker && teleportStep === 'floor' && (
        <div
          style={{
            position: 'absolute', inset: 0, background: 'rgba(11, 17, 32, 0.88)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10,
          }}
        >
          <div
            style={{
              background: '#1f2937', border: '1px solid #334155', borderRadius: 10,
              padding: 18, width: 340, maxHeight: '80%', overflowY: 'auto', color: '#e2e8f0',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
              <strong style={{ fontSize: 14 }}>Choisir un étage</strong>
              <button
                type="button"
                onClick={closeTeleportPicker}
                style={{ background: 'none', border: 'none', color: '#94a3b8', cursor: 'pointer', fontSize: 16, lineHeight: 1 }}
              >
                ✕
              </button>
            </div>
            {teleportBuildings.length > 1 && (
              <button
                type="button"
                onClick={() => setTeleportStep('building')}
                style={{
                  background: 'none', border: 'none', color: '#60a5fa', cursor: 'pointer',
                  fontSize: 11.5, padding: 0, marginBottom: 8,
                }}
              >
                ← Changer de bâtiment
              </button>
            )}
            <p style={{ fontSize: 12, color: '#94a3b8', marginTop: 4, marginBottom: 14 }}>
              L'étage choisi s'ouvrira en plan 2D : cliquez ensuite directement sur la pièce où entrer.
            </p>

            {(() => {
              // Scopé au bâtiment choisi (ou résolu automatiquement pour un
              // clic venant du graphe) -- jamais la liste brute de la zone,
              // qui mélangerait les étages de plusieurs bâtiments distincts.
              const visibleFloors = teleportBuildingId
                ? teleportFloors.filter((f) => f.buildingId === teleportBuildingId)
                : teleportFloors
              if (teleportFloorsLoading) {
                return <span style={{ fontSize: 12.5 }}>Chargement…</span>
              }
              if (visibleFloors.length === 0) {
                return <span style={{ fontSize: 12, color: '#94a3b8' }}>Aucun étage trouvé pour ce bâtiment.</span>
              }
              return (
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                  {visibleFloors.map((floor) => (
                    <button
                      key={floor.global_id}
                      type="button"
                      disabled={planRoomsLoading}
                      onClick={() => handleFloorCardClick(floor.global_id)}
                      style={{
                        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                        gap: 6, padding: '14px 8px', borderRadius: 8, border: '1px solid #334155',
                        background: '#111827', color: '#e2e8f0', cursor: planRoomsLoading ? 'wait' : 'pointer', fontSize: 12.5,
                      }}
                    >
                      <span style={{ fontSize: 20 }}>🏢</span>
                      {floor.name || floor.global_id}
                    </button>
                  ))}
                </div>
              )
            })()}
            {planRoomsLoading && (
              <p style={{ fontSize: 12, color: '#94a3b8', marginTop: 12 }}>Préparation du plan…</p>
            )}
            {teleportError && (
              <p style={{ fontSize: 12, color: '#f87171', marginTop: 12 }}>{teleportError}</p>
            )}
          </div>
        </div>
      )}

      {showTeleportPicker && teleportStep === 'plan' && (
        <div
          style={{
            position: 'absolute', top: 10, left: '50%', transform: 'translateX(-50%)', zIndex: 10,
            background: 'rgba(17, 24, 39, 0.95)', border: '1px solid #334155', borderRadius: 8,
            padding: '8px 14px', display: 'flex', alignItems: 'center', gap: 12, maxWidth: '90%',
          }}
        >
          <button
            type="button"
            onClick={() => setTeleportStep('floor')}
            style={{ background: 'none', border: 'none', color: '#93c5fd', cursor: 'pointer', fontSize: 12.5 }}
          >
            ← Étages
          </button>
          <span style={{ fontSize: 12.5, color: '#e2e8f0' }}>
            Vue de dessus — cliquez sur une pièce pour y entrer
          </span>
          {teleportError && <span style={{ fontSize: 12, color: '#f87171' }}>{teleportError}</span>}
          <button
            type="button"
            onClick={closeTeleportPicker}
            style={{ background: 'none', border: 'none', color: '#94a3b8', cursor: 'pointer', fontSize: 16, lineHeight: 1 }}
          >
            ✕
          </button>
        </div>
      )}
    </div>
  )
}
