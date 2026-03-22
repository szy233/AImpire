# Data Analysis

> Analyze datasets and generate insights through natural language.

**Status**: 🔜 Coming soon
**Mode color**: `#c97dff`
**Mode icon**: 📊

---

## Planned Capabilities

- Load and inspect CSV, JSON, Parquet datasets
- Run statistical analysis and descriptive summaries
- Generate charts and visualizations (returned as images)
- Filter, group, aggregate data with natural language queries
- Export processed data and reports

## Planned Tools

| Tool | Description |
|---|---|
| `load_dataset` | Load a file into a pandas DataFrame |
| `describe_data` | Summary statistics and schema |
| `run_query` | Execute a pandas/SQL query |
| `plot_chart` | Generate a chart (line, bar, scatter, heatmap) |
| `export_data` | Export processed data to CSV/Excel |
| `run_python` | Execute arbitrary Python in a sandboxed environment |

## Design Notes

Will use a sandboxed Python execution environment (subprocess with resource limits). Charts will be returned as base64 PNG and rendered inline in the chat. Large datasets will be summarized before being passed to Claude.

## Contributing

Interested in building this? See [CONTRIBUTING.md](../../CONTRIBUTING.md) for the feature development guide.
