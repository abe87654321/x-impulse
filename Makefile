# 常用命令。编码 agent 也读这个文件来知道怎么跑测试。
.PHONY: help dev up down reset migrate test test-unit test-int lint typecheck schema-check clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

dev: ## 安装开发依赖（不含 ML 组）
	pip install -e ".[dev]"

up: ## 起数据库与向量库
	docker compose up -d
	@echo "等待 postgres 就绪…"
	@until docker compose exec -T postgres pg_isready -U kb >/dev/null 2>&1; do sleep 1; done
	@echo "就绪"
	$(MAKE) migrate

migrate: ## 应用增量迁移（按文件名顺序，需数据库已启动）
	@for f in migrations/*.sql; do \
		[ -e "$$f" ] || continue; \
		echo "apply $$f"; \
		docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U kb -d kb < "$$f"; \
	done

down: ## 停服务（保留数据）
	docker compose down

reset: ## 停服务并删除数据卷 —— schema.sql 会重新执行
	docker compose down -v
	$(MAKE) up

test: test-unit test-int ## 全部测试

test-unit: ## 单元测试（不需要数据库）
	pytest -m "not integration" -v

test-int: ## 集成测试（需要数据库）
	DATABASE_URL=postgresql://kb:dev@localhost/kb pytest -m integration -v

lint: ## ruff 检查
	ruff check .
	ruff format --check .

typecheck: ## mypy —— 权限模块是安全关键，必须干净
	mypy kb/identity

schema-check: ## 验证建表数与 TD-01 一致
	@COUNT=$$(psql postgresql://kb:dev@localhost/kb -tAc \
		"SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"); \
	echo "建表数：$$COUNT"; \
	test "$$COUNT" -eq 29 || { echo "表数不是 29，schema 与 TD-01 不一致"; exit 1; }

clean: ## 清缓存
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache
