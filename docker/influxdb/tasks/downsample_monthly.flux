import "date"

option task = {
    name:   "governance_downsample_monthly",
    every:  1d,
    offset: 6h,
}

// Processa o mês calendário anterior completo.
// Reprocessamento diário é idempotente: sobrescreve o mesmo ponto mensal.
// monthStop muda apenas no dia 1 → o dado do mês anterior é finalizado.
monthStop  = date.truncate(t: now(), unit: 1mo)
monthStart = date.sub(d: 1mo, from: monthStop)

counts = from(bucket: "governance_hourly")
  |> range(start: monthStart, stop: monthStop)
  |> filter(fn: (r) =>
      r._measurement == "governance_metrics" and
      r._field       == "count"
  )
  |> group(columns: ["_measurement", "_field", "source", "category", "repo", "environment", "severity"])
  |> aggregateWindow(every: 1mo, fn: sum, createEmpty: false)

gauges = from(bucket: "governance_hourly")
  |> range(start: monthStart, stop: monthStop)
  |> filter(fn: (r) =>
      r._measurement == "governance_metrics" and
      r._field       != "count"
  )
  |> group(columns: ["_measurement", "_field", "source", "category", "repo", "environment", "severity"])
  |> aggregateWindow(every: 1mo, fn: mean, createEmpty: false)

union(tables: [counts, gauges])
  |> to(bucket: "governance_monthly")
