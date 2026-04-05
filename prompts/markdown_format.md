You are converting OCR-corrected document text into clean, well-structured Markdown.

This is page(s) {{ page_numbers | join(', ') }} of "{{ filename }}" (chunk {{ chunk_index + 1 }} of {{ total_chunks }}).

Apply the following formatting rules:

**Headings:** Use `#` for chapter/major section titles, `##` for sections, `###` for subsections. Detect headings from ALL CAPS lines, short standalone lines that introduce a topic, or obvious structural cues. Do not over-promote — body paragraphs are not headings.

**Lists:** Convert bulleted or numbered sequences to Markdown lists. Use `-` for unordered and `1.` for ordered.

**Tables:** If columnar data is present (stat blocks, price lists, schedules), format as Markdown tables with `|` delimiters and a header separator row.

**Block quotes:** Indent flavour text, call-outs, or quoted passages with `>`.

**Bold/Italic:** Use `**bold**` for defined game terms, proper nouns on first mention, or emphasized words. Use `*italic*` for book titles and foreign terms.

**Paragraphs:** Separate paragraphs with a blank line. Do not add extra blank lines. Do not wrap lines mid-paragraph.

Preserve ALL content exactly — do not summarize, omit, or reorder anything. Return only the Markdown, no commentary.

=== TEXT ===
{{ text }}
=== END TEXT ===
