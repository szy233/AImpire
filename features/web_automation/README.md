# Web Automation

> Browser automation through natural language — scrape data, fill forms, interact with web pages.

**Status**: 🔜 Coming soon
**Mode color**: `#ff9d5c`
**Mode icon**: 🌐

---

## Planned Capabilities

- Navigate to URLs and interact with page elements
- Extract structured data from websites
- Fill and submit forms
- Handle login flows (with credential storage)
- Screenshot and visual verification
- Run scheduled scraping jobs

## Planned Tools

| Tool | Description |
|---|---|
| `browser_navigate` | Open a URL in a headless browser |
| `browser_click` | Click an element by selector or description |
| `browser_type` | Type into an input field |
| `browser_extract` | Extract text or data from page elements |
| `browser_screenshot` | Take a screenshot of the current page |
| `browser_wait` | Wait for an element or condition |

## Design Notes

Will use Playwright (headless Chromium) running on the cloud server. Visual elements will be described to Claude via accessibility tree + optional screenshots.

## Contributing

Interested in building this? See [CONTRIBUTING.md](../../CONTRIBUTING.md) for the feature development guide.
