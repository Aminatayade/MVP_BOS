const categories: { key: string; label: string }[] = [
  { key: 'cvc', label: 'CVC' },
  { key: 'elec', label: 'Électricité' },
  { key: 'plumbing', label: 'Plomberie' },
  { key: 'generic', label: 'Autre' },
]

export default function CategoryLegend() {
  return (
    <div className="flex items-center gap-3">
      {categories.map((c) => (
        <div key={c.key} className="flex items-center gap-2">
          <img src={`/icons/${c.key}.svg`} alt={c.label} className="w-6 h-6" />
          <span className="text-sm text-gray-300">{c.label}</span>
        </div>
      ))}
    </div>
  )
}
