option task = {
    name:   "governance_downsample_hourly",
    every:  1h,
    offset: 10m,
}

// now() em tasks = hora agendada (sempre no limite da hora).
// range [-1h, now()) processa a hora anterior completa.

counts = from(bucket: "governance_raw")
  |> range(start: -1h, stop: now())
  |> filter(fn: (r) =>
      r._measurement == "governance_metrics" and
      r._field       == "count"
  )
  |> group(columns: ["_measurement", "_field", "source", "category", "repo", "environment", "severity"])
  |> aggregateWindow(every: 1h, fn: sum, createEmpty: false)

gauges = from(bucket: "governance_raw")
  |> range(start: -1h, stop: now())
  |> filter(fn: (r) =>
      r._measurement == "governance_metrics" and
      r._field       != "count"
  )
  |> group(columns: ["_measurement", "_field", "source", "category", "repo", "environment", "severity"])
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)

union(tables: [counts, gauges])
  |> to(bucket: "governance_hourly")
