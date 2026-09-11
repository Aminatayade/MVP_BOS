import { useEffect, useRef, useState } from 'react'

export type DisciplineOption = { value: string; label: string }

type Props = {
  options: DisciplineOption[]
  selected: string[]
  onChange: (next: string[]) => void
}

/**
 * Sélecteur multi-disciplines (cases à cocher dans un panneau flottant) --
 * remplace l'ancien `<select>` à valeur unique : une maquette réelle mixte
 * (ex: un fichier CVSE contenant Chauffage+Ventilation+Sanitaire+Électricité)
 * doit pouvoir se voir assigner plusieurs disciplines à la fois, chacune
 * apportant ses propres règles de validation (voir bim_validator_service.py).
 */
export default function DisciplinePicker({ options, selected, onChange }: Props) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    const handleClickOutside = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [open])

  const toggle = (value: string) => {
    onChange(selected.includes(value) ? selected.filter((v) => v !== value) : [...selected, value])
  }

  const summary =
    selected.length === 0
      ? 'Sélectionner…'
      : options
          .filter((o) => selected.includes(o.value))
          .map((o) => o.label)
          .join(', ')

  return (
    <div ref={rootRef} style={{ position: 'relative', display: 'inline-block' }}>
      <button
        type="button"
        className="input"
        style={{ width: 'auto', maxWidth: 260, padding: '5px 8px', fontSize: 12.5, textAlign: 'left', cursor: 'pointer' }}
        onClick={() => setOpen((v) => !v)}
        title={summary}
      >
        {summary.length > 34 ? `${summary.slice(0, 34)}…` : summary} ▾
      </button>
      {open && (
        <div
          style={{
            position: 'absolute',
            top: '100%',
            left: 0,
            marginTop: 4,
            zIndex: 20,
            background: '#fff',
            border: '1px solid var(--gray-300)',
            borderRadius: 6,
            boxShadow: '0 4px 16px rgba(15, 43, 70, 0.18)',
            padding: 8,
            width: 220,
            maxHeight: 260,
            overflowY: 'auto',
          }}
        >
          {options.map((option) => (
            <label
              key={option.value}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '4px 4px',
                fontSize: 12.5,
                color: 'var(--gray-900)',
                cursor: 'pointer',
              }}
            >
              <input
                type="checkbox"
                checked={selected.includes(option.value)}
                onChange={() => toggle(option.value)}
              />
              {option.label}
            </label>
          ))}
        </div>
      )}
    </div>
  )
}
