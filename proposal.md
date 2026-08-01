# 2026-08-01 迭代实现总结

> 盘点时间：2026-08-01
>
> 实施基线：`main` 分支 `7d0e445 update`
>
> 实施范围：Project 转 Workspace、v1 原地升级、事务回滚、协议同步和缓存治理

## 总览

本次改动解决两个实际使用问题：

1. 已经初始化为 Project 的目录，无法直接转换成包含多个子仓库的 Workspace。
2. v1 Bank 原地迁移到 v2 时，如果目标目录已经存在但缺少 v2 必需的 `attention.md`，迁移会被校验阻断。

对应的实现原则是：转换和迁移都必须显式触发、先预览再确认；涉及目录、Catalog 和本地 Binding 的多处变更时，通过事务快照和回滚避免留下部分成功状态；已有 Project Bank 保留为恢复副本，不做破坏性删除。

## 一、支持 Project 转换为 Workspace

### 改了什么

- 为 `mb init` 增加 `--convert-project` 参数，正式命令形态为：

  ```bash
  mb init --workspace --name <name> --convert-project
  ```

- 预览阶段会识别 Workspace 根目录当前的精确 Project Binding，并在输出中增加 `project_conversion`，包含来源 Project ID 和 Bank 路径。
- 应用阶段仍要求 `--confirm-members`，并新增以下防误用校验：
  - `--convert-project` 必须和 `--workspace` 一起使用；
  - 当前根目录必须精确绑定到一个 Project；
  - 已绑定为 Project 的根目录不能省略 `--convert-project` 直接确认 Workspace 初始化；
  - Workspace 根目录不能同时作为成员 Project；
  - 成员仓库已有 Binding 时，必须与计划中的 Project 身份一致。
- 新增 `convert_project_to_workspace()`，完成完整转换流程：
  - 校验来源 Project Bank；
  - 将来源 Bank 完整复制为新的 Workspace Bank；
  - 发现或创建各成员 Project，并初始化各自的 Bank；
  - 保留原来的 Project 记录和 Bank，避免破坏共享该身份的其他 clone/worktree；
  - 新增 Workspace 记录并登记成员 Project；
  - 将根目录 Binding 从 Project 原子替换为 Workspace；
  - 返回事务 ID、来源 Project ID 和保留的来源 Bank 路径。
- 为底层状态管理补充 `BindingStore.rebind()`：仅当当前 Binding 与预期完全一致时才替换，防止并发状态漂移被静默覆盖。
- Workspace Bank 复制使用临时目录、同目录重命名和目录 `fsync`；复制时忽略锁文件和 Python 缓存，并拒绝来源 Bank 中的符号链接。
- 转换前备份 Catalog 和 Bindings；失败时恢复元数据并删除本次创建的 Bank。如果回滚无法确认，会返回明确的高等级错误，而不是把状态报告为成功。
- CLI 成功输出增加 `transaction_id`、`source_project_id` 和 `source_bank`，便于审计和恢复。

### 为什么这么改

普通 Workspace 初始化原本会拒绝已经绑定为 Project 的根目录。但在“外层目录承载整体上下文、内部多个 Git 仓库分别作为独立项目”的场景中，外层目录早期可能已经被误建或先建成 Project，需要一个正式的升级路径。

直接编辑 `catalog.json` 和 `bindings.json` 存在三个风险：

- Bank 内容可能没有迁移到 Workspace，导致上下文丢失；
- Catalog、Bindings 和实际 Bank 目录可能只更新一部分；
- 成员仓库的远程身份和既有 Project 记录可能被重复创建或错误覆盖。

因此本次改动把转换设计成显式、可预览、需确认的事务操作，并保留原 Project 记录和 Bank。这样既能完成当前根目录的作用域转换，也不会破坏其他设备、clone、worktree 或 Workspace 仍在使用的稳定 Project 身份。

### 测试覆盖

`tests/test_v2_workspace.py` 新增了两类核心测试：

- 预览与成功应用：验证错误参数会被拒绝、预览不写状态、来源 Bank 内容被保留并复制、根目录和成员 Binding 正确、事务标记为 `committed`；
- 失败回滚：注入 Binding 替换失败，验证 Catalog 和 Bindings 恢复为原始字节、来源 Bank 仍存在、新建 Workspace/成员 Bank 被清理、事务标记为 `rolled_back`。

## 二、修复原地 v1 到 v2 迁移

### 改了什么

- 在 `bank.py` 新增 `upgrade_bank_to_v2()`：
  - 先按旧版 Bank 合约校验目录；
  - 仅在缺失时写入模板 `attention.md`；
  - 重新读取并校验 UTF-8 内容；
  - 对 Bank 目录执行 `fsync`，保证新增文件的持久性。
- `_copy_bank()` 遇到已经存在的目标 Bank 时，不再直接执行严格的 v2 校验，而是先做兼容升级，再校验完整 v2 合约。
- `apply_v2_migration()` 在事务开始前识别哪些原地目标缺少 `attention.md`，并把这些“原本不存在的文件”写入回滚清单。
- 如果后续 Catalog、Bindings 或配置写入失败，回滚会删除迁移期间补出的 `attention.md`，恢复迁移前状态。

### 为什么这么改

v2 把 `attention.md` 作为必需 Bank 文件，但部分 v1 Bank 已经位于最终的 v2 目标路径。原逻辑看到目标目录存在时只执行 `validate_v2_bank()`，导致这些内容有效但缺少新文件的 Bank 无法迁移。

简单地在校验前创建文件还不够，因为文件创建发生在真实迁移事务中：如果后面的状态写入失败，遗留的 `attention.md` 会让一次失败的迁移仍然改变用户数据。因此新增文件也必须进入事务回滚边界。

### 测试覆盖

`tests/test_v2_migration.py` 新增原地旧版 Bank fixture，并覆盖：

- dry-run 不创建 `attention.md`，也不写 Catalog；
- apply 只补齐 `attention.md`，原有 `status.md` 字节保持不变，来源仍被保留；
- 注入后续 Binding 写入失败时，新增的 `attention.md` 和其他状态文件全部回滚，事务标记为 `rolled_back`。

## 三、文档和命令面同步

- `README.md` 的命令摘要加入 `init --workspace ... --convert-project`。
- `SKILL.md` 明确 Project 到 Workspace 的转换协议：先展示来源 Project 和候选成员，得到确认后才能应用，并保留来源 Bank 供恢复。
- `.gitignore` 新增 `__pycache__/` 和 `*.py[cod]`，避免后续 Python 运行继续产生新的缓存变更。

这些修改用于让 CLI、Agent 操作协议和用户文档保持一致，避免功能已经存在但调用方仍按普通 Workspace 初始化执行。

## 四、Python 缓存治理

仓库历史中共有 34 个已跟踪的 `scripts/mb_lite/__pycache__/*.pyc`。本轮将它们全部从工作树移除，并通过 `.gitignore` 阻止后续 Python 运行再次引入缓存。`7d0e445` 中的 `.omc` 文件未擅自清理，因为其项目资产归属尚无明确约定。

## 文件变更归类

| 类别 | 文件 | 作用 |
| --- | --- | --- |
| CLI 与协议 | `scripts/mb.py`、`README.md`、`SKILL.md` | 暴露转换参数、预览/确认约束和审计输出 |
| Workspace 转换 | `scripts/mb_lite/workspace.py` | 规划成员、复制 Bank、事务应用与失败回滚 |
| 元数据操作 | `scripts/mb_lite/bindings.py` | 受条件约束的 Binding 替换 |
| v2 迁移兼容 | `scripts/mb_lite/bank.py`、`scripts/mb_lite/migrate_v2.py` | 补齐 `attention.md` 并纳入事务回滚 |
| 自动化测试 | `tests/test_v2_workspace.py`、`tests/test_v2_migration.py` | 覆盖预览、应用、数据保留和故障回滚 |
| 缓存管理 | `.gitignore`、34 个已跟踪的 `.pyc` | 忽略未来缓存并移除历史缓存 |

## 变更后的行为边界

- Project 转 Workspace 不是普通初始化的隐式分支，必须显式传入 `--convert-project`。
- 预览保持只读；真实转换仍以 `--confirm-members` 作为写入边界。
- 转换保留来源 Project 记录和 Bank，只把当前根 Binding 改为 Workspace。
- v1 原地迁移只补齐缺失的 v2 必需文件，不覆盖已有 Bank 内容。
- 转换和迁移失败时，都应恢复元数据并清理本次新建内容；无法确认回滚时以错误结束。
