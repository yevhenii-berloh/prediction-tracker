# DeepSeek Extractor Switch — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move production prediction extraction from `openai/gpt-4o-mini` (a code default nobody chose) to `deepseek/deepseek-v4-flash` (the eval v2 winner), and make the deployed model impossible to set by accident.

**Architecture:** Three small code changes — DeepSeek as the `Settings` default, a validator that refuses an empty `LLM_API_KEY`, and an explicit `temperature=0.0` for the extractor client — followed by an operational rollout: merge the branch that carries the extractor robustness fixes, write three keys into the S3 secrets `.env`, confirm on the live box what production was actually running, deploy, then run one ingestion cycle as the gate.

**Tech Stack:** Python 3.14, pydantic-settings v2, litellm, pytest (`asyncio_mode = "auto"`), ruff, complexipy, Docker Compose on EC2, AWS CLI.

**Design:** [2026-08-21-deepseek-switch-design.md](2026-08-21-deepseek-switch-design.md) · **Approved card:** [2026-08-21-deepseek-switch-review-card.md](2026-08-21-deepseek-switch-review-card.md)

## Global Constraints

- Model id is exactly `deepseek-v4-flash` with provider `deepseek`. `LLMClient` composes them into the litellm id `deepseek/deepseek-v4-flash`.
- Extractor temperature is exactly `0.0`. Eval v2 measured DeepSeek at 0.0, not at the `LLMClient` default of 0.1.
- Only the ingestion extractor changes provider. The verifier, the query planner and the answer generator stay hardcoded to `gemini/gemini-3.1-flash-lite-preview` in `factory.py`; embeddings stay on OpenAI `text-embedding-3-small`.
- Test command is `.venv/bin/python -m pytest tests/ -q`. Baseline before this plan: **596 passed**.
- Line length 100 (ruff). Cognitive complexity gate: `complexipy --diff HEAD --ratchet src` must stay green.
- Never print a secret value to the terminal. Keys move by shell substitution, not by copy-paste.
- Prod secrets live in the S3 `.env` object, written only through `deploy/secrets.sh`.
- Red zone (own commit, read line by line): `config.py`, `factory.py`. Green zone: tests, `.env.example`, docs.

---

### Task 1: Merge the extractor robustness fixes into `main`

**Scope:** Get the seven production commits from the eval session onto `main`. The box pulls `main`, and those commits are what make an unknown response shape a recorded failure instead of a lost post. Nothing else in this plan is safe to deploy without them.

**Files:**
- No file edits. Git operations only.

**Interfaces:**
- Consumes: nothing.
- Produces: `main` containing `ExtractionOutcome`, the bare-array tolerance in `parse_extraction_response`, and the cursor-holds-on-failure branch in `IngestionOrchestrator`.

- [ ] **Step 1: Confirm the branch suite is green**

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: `596 passed`.

- [ ] **Step 2: Confirm the merge is a fast-forward**

```bash
git log --oneline HEAD..main | wc -l
```

Expected: `0`. If it is not 0, stop — `main` has commits the branch does not, and this plan's merge step needs rewriting.

- [ ] **Step 3: Merge into main**

```bash
git checkout main
git pull --ff-only
git merge --ff-only feat/extraction-eval-v2
```

- [ ] **Step 4: Run the suite on main**

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: `596 passed`.

- [ ] **Step 5: Push**

```bash
git push origin main
git log --oneline origin/main -1
```

Expected: the last commit is `docs(deepseek-switch): дизайн і картка G1 переходу екстрактора на DeepSeek`.

---

### Task 2: `Settings` — DeepSeek defaults and a hard stop on an empty key

**Scope:** Make the code default name the same model the environment names, and turn an empty `LLM_API_KEY` into a startup crash. Today the empty key flows into litellm, which quietly falls back to the provider's own env var — the exact path that put `gpt-4o-mini` into production.

**Files:**
- Modify: `src/prophet_checker/config.py:9-11` (defaults), and add a validator after the field block
- Modify: `tests/test_config.py` (two new tests)
- Modify: `tests/test_factory.py:77,83,90` (three `Settings(...)` calls that do not pass a key)
- Modify: `.env.example:10-12`

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings.llm_provider == "deepseek"`, `Settings.llm_model == "deepseek-v4-flash"`, and a `pydantic.ValidationError` from `Settings(...)` when `llm_api_key` is empty.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
import pytest
from pydantic import ValidationError


def test_settings_rejects_empty_llm_api_key(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(ValidationError, match="LLM_API_KEY"):
        Settings(_env_file=None)


def test_settings_defaults_to_deepseek(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    settings = Settings(_env_file=None, llm_api_key="sk-test")
    assert settings.llm_provider == "deepseek"
    assert settings.llm_model == "deepseek-v4-flash"
```

`_env_file=None` matters: without it pydantic-settings reads the repo-root `.env`, and the test would pass or fail depending on a gitignored file.

- [ ] **Step 2: Run them to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_config.py -v
```

Expected: `test_settings_rejects_empty_llm_api_key` FAILS (no `ValidationError` raised — the empty default is accepted), `test_settings_defaults_to_deepseek` FAILS (`assert 'openai' == 'deepseek'`).

- [ ] **Step 3: Change the defaults and add the validator**

In `src/prophet_checker/config.py`, change the import line at the top:

```python
from pydantic import model_validator
from pydantic_settings import BaseSettings
```

Replace lines 9-11:

```python
    llm_provider: str = "deepseek"
    llm_model: str = "deepseek-v4-flash"
    llm_api_key: str = ""
```

Add this method to `Settings`, directly after `model_config`:

```python
    @model_validator(mode="after")
    def _require_llm_api_key(self) -> "Settings":
        # Порожній ключ не падає сам: litellm мовчки бере env-змінну провайдера
        # (OPENAI_API_KEY для openai) — саме так прод і з'їхав на gpt-4o-mini.
        if not self.llm_api_key:
            raise ValueError(
                f"LLM_API_KEY is empty; set it for llm_provider={self.llm_provider!r}"
            )
        return self
```

`mode="after"` is required. A `field_validator` on `llm_api_key` would not run at all when the field falls back to its default, which is the case this check exists for.

- [ ] **Step 4: Fix the three `Settings(...)` calls that rely on ambient config**

In `tests/test_factory.py`, lines 77, 83 and 90 build `Settings` without a key. Add it so the tests stop depending on a gitignored `.env`:

```python
async def test_build_bot_disabled_returns_none():
    settings = Settings(bot_enabled=False, llm_api_key="sk-test")
    async with AsyncExitStack() as stack:
        assert await build_bot(settings, stack, MagicMock()) is None


async def test_build_bot_enabled_without_token_fails_fast():
    settings = Settings(bot_enabled=True, telegram_bot_token="", llm_api_key="sk-test")
    async with AsyncExitStack() as stack:
        with pytest.raises(ValueError, match="telegram_bot_token"):
            await build_bot(settings, stack, MagicMock())


async def test_build_bot_registers_stop_on_stack():
    settings = Settings(
        bot_enabled=True, telegram_bot_token="123456:TEST-TOKEN", llm_api_key="sk-test"
    )
```

- [ ] **Step 5: Run the full suite**

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: `598 passed`.

- [ ] **Step 6: Update `.env.example`**

Replace lines 10-12:

```
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-v4-flash
LLM_API_KEY=sk-your-deepseek-key-here
```

Change the comment above the block (line 9) to name the eval that chose the model:

```
# -- Production LLM (single provider) — extractor; deepseek-v4-flash chosen by extraction eval v2 (2026-08-15) --
```

- [ ] **Step 7: Add the key to your own `.env`**

`.env` is gitignored, and ~18 scripts build `Settings()` for reasons unrelated to extraction (retrieval evals, embed corpus, verification cycle). Without this line they all stop working locally.

```bash
grep -q '^LLM_API_KEY=' .env || printf 'LLM_API_KEY=%s\n' "$(grep '^DEEPSEEK_API_KEY=' .env | cut -d= -f2-)" >> .env
grep -oE '^LLM_[A-Z_]+' .env
```

Expected: `LLM_API_KEY`. The value is never printed.

- [ ] **Step 8: Lint and complexity gate**

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/complexipy --diff HEAD --ratchet src
```

Expected: all three clean.

- [ ] **Step 9: Commit (red zone — own commit)**

```bash
git add src/prophet_checker/config.py tests/test_config.py tests/test_factory.py .env.example
git commit -m "feat(config): deepseek-v4-flash за замовчуванням, порожній LLM_API_KEY падає на старті"
```

---

### Task 3: Extractor client at temperature 0.0

**Scope:** `LLMClient` defaults to `temperature=0.1`, but eval v2 measured DeepSeek at 0.0. Production should run the configuration that was measured, so `build_orchestrator` passes the temperature explicitly.

**Files:**
- Modify: `src/prophet_checker/factory.py:39-43`
- Modify: `tests/test_factory.py` (one new test)

**Interfaces:**
- Consumes: `Settings.llm_provider`, `Settings.llm_model`, `Settings.llm_api_key` from Task 2.
- Produces: `build_orchestrator` constructs `LLMClient(..., temperature=0.0)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_factory.py`, after `test_build_orchestrator_returns_orchestrator`:

```python
async def test_build_orchestrator_extractor_runs_at_zero_temperature(monkeypatch):
    settings = _settings_with_test_env(monkeypatch)

    with (
        patch("prophet_checker.factory.TelegramClient") as MockTg,
        patch("prophet_checker.factory.LLMClient") as MockLLM,
    ):
        mock_tg_instance = MockTg.return_value
        mock_tg_instance.start = AsyncMock()
        mock_tg_instance.disconnect = AsyncMock()

        async with AsyncExitStack() as stack:
            await build_orchestrator(settings, stack)

    assert MockLLM.call_args.kwargs["temperature"] == 0.0
```

- [ ] **Step 2: Run it to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_factory.py::test_build_orchestrator_extractor_runs_at_zero_temperature -v
```

Expected: FAIL with `KeyError: 'temperature'` — `build_orchestrator` does not pass the argument yet.

- [ ] **Step 3: Pass the temperature**

In `src/prophet_checker/factory.py`, replace the `LLMClient(...)` call inside `build_orchestrator`:

```python
    llm = LLMClient(
        provider=settings.llm_provider,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        temperature=0.0,  # eval v2 міряв екстракцію на 0.0, дефолт клієнта 0.1
    )
```

- [ ] **Step 4: Run the full suite**

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: `599 passed`.

- [ ] **Step 5: Commit (red zone — own commit)**

```bash
git add src/prophet_checker/factory.py tests/test_factory.py
git commit -m "feat(factory): прод-екстрактор на temperature 0.0 — як міряв eval v2"
```

---

### Task 4: Local smoke — 10 real posts through DeepSeek

**Scope:** Prove the API path works before spending production money or moving channel cursors: a real DeepSeek key, the real production prompt, the real parser. This catches a wrong model id, a rejected key, or a response envelope the parser cannot read. It does not measure quality — eval v2 did that on 130 posts.

**Files:**
- Create: `/private/tmp/claude-501/-Users-evgenijberlog-Brain/3b84ccb1-8e79-4b12-b1c0-219f09b98409/scratchpad/deepseek_smoke.py` — throwaway, **not committed**. The repo does not need a one-time script.

**Interfaces:**
- Consumes: `PredictionExtractor.extract(text, person_id, document_id, person_name, published_date) -> ExtractionOutcome` and `LLMClient(provider, model, api_key, temperature)`.
- Produces: evidence only — a pass/fail line and the extracted claims.

- [ ] **Step 1: Write the smoke script**

Write to the scratchpad directory (not the repo):

```python
import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from prophet_checker.analysis.extractor import PredictionExtractor
from prophet_checker.llm import LLMClient

REPO = Path("/Users/evgenijberlog/Brain/prediction-tracker")
DATASET = REPO / "scripts" / "data" / "extraction" / "eval_dataset_2026-08-12.json"


async def main() -> None:
    load_dotenv(REPO / ".env")
    key = os.environ["DEEPSEEK_API_KEY"]
    posts = json.loads(DATASET.read_text())["posts"][:10]

    llm = LLMClient(
        provider="deepseek", model="deepseek-v4-flash", api_key=key, temperature=0.0
    )
    extractor = PredictionExtractor(llm)

    failures = 0
    for post in posts:
        outcome = await extractor.extract(
            text=post["text"],
            person_id=post["author"],
            document_id=post["id"],
            person_name=post["author"],
            published_date=post["published_date"],
        )
        if outcome.failed:
            failures += 1
            print(f"FAILED {post['id']}: {outcome.error}")
            continue
        print(f"ok {post['id']}: {len(outcome.predictions)} claims")
        for prediction in outcome.predictions:
            print(f"    - {prediction.claim_text}")

    print(f"\nfailures: {failures}/10")
    print(f"tokens: prompt={llm.prompt_tokens} completion={llm.completion_tokens}")


asyncio.run(main())
```

- [ ] **Step 2: Run it**

```bash
.venv/bin/python /private/tmp/claude-501/-Users-evgenijberlog-Brain/3b84ccb1-8e79-4b12-b1c0-219f09b98409/scratchpad/deepseek_smoke.py
```

Expected: ten `ok …` lines and `failures: 0/10`. Non-zero token counts confirm the call really reached DeepSeek.

- [ ] **Step 3: Read the claims**

Gate: the printed claims are in the post's language and are recognisable predictions, not echoes of the whole post. If every post yields zero claims, stop — that is the silent-failure shape eval v2 found twice, and it means the response envelope is wrong, not that the posts are empty.

- [ ] **Step 4: Nothing to commit**

The script stays in the scratchpad. Do not add it to `scripts/`.

---

### Task 5: Write the three keys into the S3 secrets `.env`

**Scope:** Make the deployed configuration explicit. `secrets.sh set` does a round-trip on a single key, so the other 13 keys — the RDS URL, the Telegram tokens — stay untouched.

**Files:**
- Modify: `s3://prophet-secrets-secretsbucket-xvwjyeqseybq/.env` (via `deploy/secrets.sh` only)

**Interfaces:**
- Consumes: `DEEPSEEK_API_KEY` from the local `.env`.
- Produces: `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY` present in the prod `.env` object.

- [ ] **Step 1: Dry-run one key first**

```bash
./deploy/secrets.sh -n set LLM_PROVIDER deepseek
```

Expected: a `DRY:` line and the resulting key list. Nothing is written.

- [ ] **Step 2: Write provider and model**

```bash
./deploy/secrets.sh -y set LLM_PROVIDER deepseek
./deploy/secrets.sh -y set LLM_MODEL deepseek-v4-flash
```

- [ ] **Step 3: Write the key without printing it**

```bash
./deploy/secrets.sh -y set LLM_API_KEY "$(grep '^DEEPSEEK_API_KEY=' .env | cut -d= -f2-)"
```

- [ ] **Step 4: Verify by names only**

```bash
./deploy/secrets.sh list
```

Expected: the key list now contains `LLM_API_KEY`, `LLM_MODEL`, `LLM_PROVIDER` alongside the original 13. Values are not printed.

---

### Task 6: Start the environment, pre-check what production ran, deploy

**Scope:** Raise the paused environment and, before changing anything on the box, read the effective model out of the still-old container. That output is the only direct evidence of what production was extracting with; the design's §1 is otherwise an inference from config files.

**Files:**
- No file edits. Operational steps.

**Interfaces:**
- Consumes: `main` from Tasks 1-3, the S3 `.env` from Task 5.
- Produces: a running box on the new image with the new configuration.

- [ ] **Step 1: Start RDS and the box**

```bash
./deploy/start.sh
```

Expected: RDS reaches `available`, the instance reaches `running`, and a new public IP is printed. The IP changes on every start — there is no Elastic IP.

- [ ] **Step 2: If SSH hangs, refresh the security group**

Only if your own IP changed since the last session:

```bash
MYIP=$(curl -s https://checkip.amazonaws.com)/32
aws ec2 authorize-security-group-ingress --region eu-central-1 \
  --group-id sg-0270bcebe877d72be --protocol tcp --port 22 --cidr "$MYIP"
```

- [ ] **Step 3: The pre-check — what has production been running?**

The container is still the old image with the old `.env`, so this reads the live production configuration:

```bash
./deploy/connect.sh -- python -c "from prophet_checker.config import get_settings; s=get_settings(); print(s.llm_provider, s.llm_model)"
```

Expected: `openai gpt-4o-mini`.

**If it prints anything else, stop here.** Record the exact output, do not run `deploy.sh`, and revise §1 of the design — the whole rationale rests on this line.

- [ ] **Step 4: Deploy**

```bash
./deploy/deploy.sh
```

Expected: `git pull` lands the Task 1-3 commits, `migrate exit code: 0`, `health: 200`, `OK: деплой завершено на боксі`. `--force-recreate` is what makes the container re-read the changed `env_file`.

- [ ] **Step 5: Confirm the new configuration is live**

```bash
./deploy/connect.sh -- python -c "from prophet_checker.config import get_settings; s=get_settings(); print(s.llm_provider, s.llm_model)"
```

Expected: `deepseek deepseek-v4-flash`.

---

### Task 7: One ingestion cycle — the production gate

**Scope:** Run the pipeline end to end on real posts and read the report. This is the gate the design commits to: no failed posts, and predictions actually extracted.

**Files:**
- No file edits. Operational step that writes to the production database.

**Interfaces:**
- Consumes: the deployed box from Task 6.
- Produces: a `CycleReport` with per-channel counters.

- [ ] **Step 1: Run the cycle**

The backlog has been growing since 17 Aug, so this processes several hundred posts in one pass. At roughly $0.001 per post that is well under a dollar, but it is not instant — keep the timeout generous.

```bash
./deploy/ingest.sh --timeout 1800
```

- [ ] **Step 2: Read the gate out of the report**

Expected in the printed `CycleReport`: every channel has `posts_failed: 0`, and the total `predictions_extracted` is greater than zero.

If `posts_failed > 0`: the channel stopped on the offending post and its cursor did not move, so nothing is lost. Pull the reason before deciding anything:

```bash
./deploy/logs.sh | grep -iE "extraction|Unparsable|failed" | tail -40
```

`UnparsableResponse` means DeepSeek returned an envelope the parser does not accept — that is a rollback trigger, not a retry.

- [ ] **Step 3: Rollback path, if the gate fails**

```bash
./deploy/secrets.sh -y set LLM_PROVIDER openai
./deploy/secrets.sh -y set LLM_MODEL gpt-4o-mini
./deploy/secrets.sh -y set LLM_API_KEY "$(grep '^OPENAI_API_KEY=' .env | cut -d= -f2-)"
./deploy/deploy.sh -y
```

Two minutes, no rebuild of intent: the code default stays DeepSeek, the environment decides.

- [ ] **Step 4: Sanity-check the data**

```bash
./deploy/psql.sh --stats
```

Expected: the prediction count grew by roughly the cycle's `predictions_extracted`.

- [ ] **Step 5: Leave the box running**

Per the approved card, the environment stays up after verification. Do not run `deploy/stop.sh`.

---

### Task 8: Record the switch

**Scope:** Close the loop in the two places that go stale first — the progress log, and the `CLAUDE.md` sentence that told everyone production ran Flash Lite.

**Files:**
- Modify: `progress.md` (new entry under the current session block)
- Modify: `CLAUDE.md:69`

**Interfaces:**
- Consumes: the numbers from Task 7.
- Produces: documentation only.

- [ ] **Step 1: Fix the claim in `CLAUDE.md`**

Line 69 currently ends with "evals selected Gemini 3.1 Flash Lite as the production extraction model" — the sentence that made the drift invisible. Replace that clause:

```
Model is chosen via `.env` (`config.py` defaults to `deepseek/deepseek-v4-flash`); extraction eval v2 (2026-08-15) selected DeepSeek v4 Flash for extraction, and the verifier, planner and answer generator remain on Gemini 3.1 Flash Lite, hardcoded in `factory.py`.
```

- [ ] **Step 2: Add the progress entry**

Append to `progress.md`, following the style of the surrounding entries — prose, numbers, what was decided and why:

```markdown
- **Прод-екстрактор переведено на DeepSeek (2026-08-21).** Рішення, підняте прапорцем евалу v2 15 серпня, ухвалене. Заразом виявлено дрейф: прод-`.env` у S3 **ніколи** не задавав `LLM_PROVIDER`/`LLM_MODEL` (перевірено всі 7 версій обʼєкта), тож екстракція йшла на `openai/gpt-4o-mini` з дефолтів `config.py` — модель, якої нема в жодному евалі. «Базлайн Flash Lite» у v2 порівнювався не з тим, що працювало. Перед деплоєм звірено на живому боксі: `<вивід кроку 3 задачі 6>`. Тепер дефолт коду й S3-`.env` називають ту саму модель, а порожній `LLM_API_KEY` кидає на старті — раніше він тихо провалювався в `OPENAI_API_KEY` через litellm. Екстрактор на `temperature=0.0` (як міряв евал), недетермінізм 0.20 прийнято свідомо. Цикл інжесту: `<posts_seen>` постів, `posts_failed=0`, `<predictions_extracted>` передбачень. Дизайн і картка: [`docs/extraction-eval-v2/`](docs/extraction-eval-v2/).
```

Replace every `<…>` with the real number from Task 7 before committing.

- [ ] **Step 3: Commit (green zone)**

```bash
git add progress.md CLAUDE.md
git commit -m "docs: прод-екстрактор на deepseek-v4-flash + виправлено твердження про модель у CLAUDE.md"
git push origin main
```
