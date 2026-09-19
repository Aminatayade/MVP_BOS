/**
 * FILE: features/maintenance/components/KpiSlaRatioCard.tsx
 * DESCRIPTION: Widget 2 -- ratio temps de traitement réel / SLA contractuel.
 *              Vert (--success) si <= 1 (dans les clous), rouge (--danger)
 *              sinon -- couleurs sémantiques déjà existantes dans l'app, pas
 *              de nouvelle palette.
 */
type Props = {
  ratio: number | null
}

export default function KpiSlaRatioCard({ ratio }: Props) {
  const withinSla = ratio !== null && ratio <= 1

  return (
    <div className="panel">
      <strong style={{ fontSize: 13, color: 'var(--text-h)' }}>Temps réel / SLA</strong>
      <div
        style={{
          fontSize: 32, fontWeight: 700, marginTop: 8,
          color: ratio === null ? 'var(--text-h)' : withinSla ? 'var(--success)' : 'var(--danger)',
        }}
      >
        {ratio === null ? '—' : ratio.toFixed(2)}
      </div>
      <p className="panel-hint" style={{ marginTop: 4 }}>
        {ratio === null ? 'Aucun ticket clôturé sur ce périmètre' : withinSla ? 'Sous le délai contractuel' : 'Au-delà du délai contractuel'}
      </p>
    </div>
  )
}
