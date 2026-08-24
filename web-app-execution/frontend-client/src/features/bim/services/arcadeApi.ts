/**
 * Client pour les routes BIM en lecture seule exposées à BOS Exécution par
 * l'API Gateway (voir api-gateway/bim_proxy.py). Version allégée de
 * web-app-configuration/frontend/.../arcadeApi.ts : seules les deux fonctions
 * nécessaires à la visualisation 3D (point 6 de la roadmap, volet Exécution)
 * -- pas d'upload, pas de validation/ingestion, réservées à Configurateur.
 */

import apiClient from '../../../services/apiClient'

function unwrapArcadeResult(payload: Record<string, unknown>): any[] {
  const arcade: any = payload.result ?? {}
  return arcade.result ?? arcade ?? []
}

function extractErrorMessage(error: unknown, fallback: string): string {
  const anyError = error as { response?: { data?: { error?: string } }; message?: string }
  return anyError.response?.data?.error || anyError.message || fallback
}

export interface ModelSource {
  discipline: string
  storageUrl: string
}

/**
 * Liste les fichiers IFC déjà ingérés pour une zone, un par discipline.
 * Renvoie une liste vide si rien n'a encore été ingéré côté Configurateur --
 * ce n'est pas une erreur, juste l'état attendu tant que l'ingestion n'a pas
 * eu lieu.
 */
export async function getModelSources(zone: string): Promise<ModelSource[]> {
  try {
    const resp = await apiClient.get('/api/bim/graph/sources', { params: { zone } })
    const rows = unwrapArcadeResult(resp.data)
    const sources: ModelSource[] = []
    for (const row of rows) {
      const discipline = (row as Record<string, unknown>).discipline as string | undefined
      const storageUrl = (row as Record<string, unknown>).storage_url as string | undefined
      if (discipline && storageUrl) sources.push({ discipline, storageUrl })
    }
    return sources
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to fetch model sources'))
  }
}

/**
 * Télécharge un IFC source pour le visualiseur 3D. Passe par apiClient (pas
 * une URL brute) car /api/bim/models/download exige le Bearer token Keycloak.
 */
export async function downloadModelBytes(storageUrl: string): Promise<ArrayBuffer> {
  try {
    const resp = await apiClient.get('/api/bim/models/download', {
      params: { url: storageUrl },
      responseType: 'arraybuffer',
    })
    return resp.data as ArrayBuffer
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to download IFC model'))
  }
}
