You are reconciling multiple OCR outputs of the same document page(s) {{ page_numbers | join(', ') }} from "{{ filename }}".

{% for source in sources %}
=== OCR Output from {{ source.engine }} ===
{{ source.text }}
{% endfor %}

Produce the single most accurate transcription. Where the outputs disagree, use your best judgment based on common OCR errors and English language patterns. Do NOT add, remove, or summarize content.

Return only the reconciled text.
