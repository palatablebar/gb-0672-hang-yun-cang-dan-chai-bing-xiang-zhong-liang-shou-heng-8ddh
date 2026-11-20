# 深度登记 航运舱单拆并箱与重量守恒

规模：中型。使用者：货代单证员。核心规则：主提单与分提单拆并形成有向无环关系，件数和净毛重逐层守恒。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 舱单采信 | 执行并复核舱单采信 | `POST /api/intake` | `app/modules/intake/rules.py` | `tests/test_intake.py` |
| M2 包装映射 | 执行并复核包装映射 | `POST /api/packaging` | `app/modules/packaging/rules.py` | `tests/test_packaging.py` |
| M3 拆并编组 | 执行并复核拆并编组 | `POST /api/grouping` | `app/modules/grouping/rules.py` | `tests/test_grouping.py` |
| M4 危险品检查 | 执行并复核危险品检查 | `POST /api/hazard` | `app/modules/hazard/rules.py`、`app/modules/hazard/voucher.py` | `tests/test_hazard.py`、`tests/test_voucher.py` |
| M5 净毛重核验 | 执行并复核净毛重核验 | `POST /api/weight` | `app/modules/weight/rules.py` | `tests/test_weight.py` |
| M6 报关对账 | 执行并复核报关对账 | `POST /api/reconcile` | `app/modules/reconcile/rules.py` | `tests/test_reconcile.py` |
| M7 版本封存 | 执行并复核版本封存 | `POST /api/archive` | `app/modules/archive/rules.py` | `tests/test_archive.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | 旧编码回退 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_legacy_source` |
| TRAP-02 | BOM表头 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_bom_source` |
| TRAP-04 | 分段表头 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_segment_header` |
| TRAP-05 | 缺列隔离 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_column_isolation` |
| TRAP-14 | 编号冲突 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_conflict_all_isolated` |
| TRAP-18 | 空态三分 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_three_empty_states` |
| TRAP-15 | 吨与包装换算 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/packaging/rules.py` | `tests/test_packaging.py::test_pallet_real` |
| TRAP-07 | 空值哨兵 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/packaging/rules.py` | `tests/test_packaging.py::test_sentinel_real` |
| TRAP-06 | 全角千分位 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/packaging/rules.py` | `tests/test_packaging.py::test_fullwidth_decimal` |
| TRAP-20 | 拆并依赖环 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/grouping/rules.py` | `tests/test_grouping.py::test_cycle_real` |
| TRAP-21 | 缺失父级 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/grouping/rules.py` | `tests/test_grouping.py::test_missing_parent_real` |
| TRAP-22 | 混类箱重复入区 | 同一箱计入相邻两区重复配载 | 每箱恰入一区，混类与未知类入扣留区 | `app/modules/hazard/voucher.py` | `tests/test_voucher.py::test_box_counted_once` |
| TRAP-23 | 未闭合箱拖累全批 | 整区扣留错过船期或整区放行混装不相容 | 按剩余箱能否共同证明隔离结论裁决部分扣留或整区冻结 | `app/modules/hazard/voucher.py` | `tests/test_voucher.py::test_freeze_when_closed_box_incompatible` |
| TRAP-24 | 凭证确认竞争 | 失败覆盖旧版或重跑新增第二套 | 乐观版本控制，失败保留旧版与原舱单证据，凭证按run_id唯一 | `app/store.py` | `tests/test_voucher.py::test_voucher_confirm_failure_restores_old` |

## 对账规则

原始逻辑输入=采纳行+隔离行+空行+重复表头+同值重复。业务计算按 DATA-DICTIONARY.md 分别核验，不能把上级与下级重复加总。所有隔离保留编号、原值、原始行号和稳定原因码。

## 已知不覆盖

数据来自仓内样例或用户粘贴文件，不连接外部业务生产系统。平台不执行实际资金划转或行政审批。
