# Промпт-варіанти екстракції

Кандидати system prompt для A/B-тестування через
`extraction_quality_eval.py --extraction-prompt <файл>`.

- Один файл = один повний system prompt (plain text / markdown).
- Дефолт без аргументу — продакшн-промпт `EXTRACTION_SYSTEM`
  із `src/prophet_checker/llm/prompts.py`.
- Промоція переможця = перенесення тексту в `EXTRACTION_SYSTEM`
  (єдине джерело правди для прод і eval — див. CLAUDE.md).
- Артефакт `extraction_outputs.json` фіксує шлях + sha256 промпта,
  яким зроблено прогін.

Дизайн: `docs/extraction-quality/2026-06-12-extraction-prompt-variant-design.md`
