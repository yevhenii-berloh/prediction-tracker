# `aws-deploy/` — мінімальний деплой на AWS

Статус: «воно живе» — один EC2 з Docker Compose (app+migrate), БД на
**RDS PostgreSQL (pgvector)**, доступ лише SSH-тунелем, секрети з приватного S3,
інфра як CloudFormation. (До RDS-міграції БД крутилась Postgres-контейнером на боксі.)

| Документ | Призначення |
|----------|-------------|
| [`2026-07-04-minimal-aws-deploy-design.md`](2026-07-04-minimal-aws-deploy-design.md) | Design: рішення, компроміси, acceptance |
| [`2026-07-04-minimal-aws-deploy-plan.md`](2026-07-04-minimal-aws-deploy-plan.md) | Implementation plan — 8 задач |
| [`2026-07-10-rds-migration-design.md`](2026-07-10-rds-migration-design.md) | Design: RDS-міграція БД (Phase B durability) |
| [`2026-07-10-rds-migration-plan.md`](2026-07-10-rds-migration-plan.md) | Implementation plan — 9 задач |

Артефакти: `Dockerfile`, `docker-compose.yml` (migrate/app) +
`docker-compose.override.yml` (локальний Postgres для dev),
`deploy/cloudformation/{secrets,data,compute}-stack.yaml`.

Поза скоупом (майбутнє): розклад ingestion, публічний RAG/бот — Phase B/C.

## Відхилення від плану / знахідки

- Код — source of truth; шаблони в plan.md показують структуру, але фактичні файли трохи відрізняються.
- CFN `compute` `GroupDescription` мусив бути ASCII (AWS `CreateSecurityGroup` відхиляє кирилицю/em-dash) — англійський рядок.
- user-data hardened: `set -eo pipefail` (не лише `pipefail`) — щоб fail-крок не доходив до фінального `signal 0` і стек чесно падав у `CREATE_FAILED`.
- CFN `secrets` бакет має і `DeletionPolicy: Retain`, і `UpdateReplacePolicy: Retain`.
- `RepoUrl` дефолт — реальний `https://github.com/evgeniy44/prediction-tracker.git`.
- Локальний bring-up (Task 5) зелений: postgres healthy → migrate exit 0 (6 ревізій) → app up → `/health`=200. Знахідка: app на старті живо конектиться до Telegram (MTProto) — боксу потрібен outbound (SG пускає весь outbound). Box-acceptance (реальний деплой) — за користувачем.
- Runbook-примітка: `docker compose ps` без `-a` ховає exited-контейнер `migrate`; для перевірки його exit-коду використовуй `docker compose ps -a`.
