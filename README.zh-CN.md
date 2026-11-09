# Telegram 群助手

为一个 Telegram 群部署一个独立的 Hermes 助手，用来找回群聊、查询已授权资料，并按需推送日报。

[English](README.md) · [MIT](LICENSE) · 可供 Agent 使用的技能，附配置模板与部署脚本。

## 适合谁用

适合想找回近期讨论、查询指定仓库或任务、按需订阅推特日报的小群。部署者需要 Telegram 账号、Bot、模型 API 账号和运行 Hermes 的主机。群内任何成员都能问到接入资料；私密对话需要另开实例。

## 三分钟能看到什么

已有 Python 3.13+ 和登录后的 `gh` 时：

```sh
gh repo clone bmdy6/skill-tg-group-assistant
cd skill-tg-group-assistant
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

可以看到路由、日报和隔离部署的离线测试通过。不需要 Bot、密钥或外部 API，也不会启动网关。这是本地预览，不代表 Telegram 接入完成。当前私有预览需要仓库访问权限。

## 怎么装

把仓库克隆到所用 Agent 支持的技能目录，再告诉它：

> 用 tg-group-assistant 给一个新的 Telegram 群建助手。先读 SKILL.md。

Agent 按 [SKILL.md](SKILL.md)、[用途访谈](references/interview.md) 和 [Telegram 设置清单](references/telegram-setup-task.md) 操作。可以使用浏览器自动化工具（例如 ego-browser 或 Playwright），也可以手动设置。**先关闭 Bot 隐私模式，再拉进群。**

运行时固定为 **Hermes v0.23.0、Python 3.14**，按锁文件安装：

```sh
git clone --branch v0.23.0 --depth 1 https://github.com/NousResearch/hermes-agent.git hermes-agent
cd hermes-agent
uv sync --frozen --python 3.14 --extra telegram --extra mcp
cd ..
```

回到本技能仓库，把变量设成自己的绝对路径：

```sh
export HERMES_REPO="$(cd ../hermes-agent && pwd)"
export HERMES_HOME="$HOME/.hermes-reading-group"
bash deploy/install.sh
```

不要共用个人 Hermes 实例。私下从你的密钥仓库填入 `$HERMES_HOME/.env`，权限保持 `600`。两个群白名单都只填目标群，用户白名单填允许私聊的 owner。编辑实例里的 `config.yaml` 与 `SOUL.md` 后启动：

```sh
bash "$HERMES_HOME/start.sh"
```

安装不会启动网关。重跑安装保留 `.env`、配置、人设、账号列表；会更新附带脚本和插件。模板里的配置变化不会覆盖已有实例，需要显式改实例配置。一个群使用一个 Bot 和一个 `HERMES_HOME`。

MCP 与 Jev 默认关闭。MCP 必须同时使用**限定范围的只读 token 和只读端点**，核对最终工具清单，再启用需要的服务。群工具只给 web、skills、todo、memory、session_search 与批准的只读 MCP 工具集。

## 可选能力

| 能力 | 配置方法 |
|---|---|
| 推特日报 | 在实例 `twitter/accounts.txt` 填公开账号；密钥放 `secrets/twitterapi_io.key`、权限 600；在 `.env` 填接收者、时间和时区，重跑安装。已有任务用 cron CLI 显式修改。 |
| Jev 减少 @ | 告知群成员上下文会交给外部判断服务；停网关，同时打开插件两个开关、设置 `require_mention: false`；私下注入 key。[启用与回滚](SKILL.md#可选-jev-路由)。 |
| 保活 | 如果没有 systemd，可以用一个保活循环，见 [keepalive.snippet](deploy/keepalive.snippet)；循环还需要主机开机启动机制。 |

Jev 保守参数：第一题 addressed 分数至少 **0.85**，每群 **600 秒**最多主动放行一次，默认 **10 条**上下文，总超时最多 **2 秒**。缺 key 或错误时沉默，其他两题只记录。判断记录在 `$HERMES_HOME/state/jev_router.jsonl`，保留十四天；比较 `scores` 和 `decision` 看误放行与漏放行。群和消息使用摘要引用，预览遮蔽凭据与数字；`allow` 不能当作送达证据。

## 三档验收

| 档位 | 证据 |
|---|---|
| 代码完成 | 离线测试全绿；隔离安装幂等；真实 Hermes 配置与工具面检查通过，见 [运行时检查](checks/README.md)。 |
| 真实接入通过 | 不 @ 时沉默，@ 后能引用刚才谁说过什么；重启会话保留；其他群被拒；MCP 只读；可选日报 delivery 为 delivered；Jev 冷却与故障行为符合预期。 |
| 已上线 | 保活下连续运行至少 24 小时；定时任务准时；日志无反复错误。 |

离线测试通过只能证明本地检查。运行时检查、真实接入和上线时长需要各自的证据。

## 已知坑

- Hermes v0.23.0 要 Python 3.14、`uv sync --frozen`。Python 3.13 下能运行版本命令，不代表核心依赖装齐。
- 先关闭隐私模式再入群；Bot 看不到入群前历史。
- `drop_pending_on_cold_boot: false` 保留重启期间消息；网关启动后不要再调 `getUpdates`，同一 token 只能有一个消费者。
- 长期记忆是整个实例共用，包含私聊；人设不能隔离群聊与私聊的私密资料。
- cron 脚本拿不到环境变量；日报从安装位置推导 home，优先读取密钥文件。
- twitterapi.io 试用账号有频率限制，请求至少间隔 5.5 秒，429 只重试一次；`code` 字段可能是字符串，`"0"` 也是成功。
- 日报去重记录的是交给 Hermes 的候选，不代表送达；发送失败后重跑可能跳过已交接候选。
- `pgrep -f` 会匹配检查命令自己。启动脚本用 Hermes 的实例 runtime lock，保活循环仍需要开机启动机制。
- Jev 需要 `in_process`、真实 gateway 和 SessionStore。插件用 `jev_model`，避免与保留根名 `model` 冲突。

## 目录

`SKILL.md`、`references/` 是操作指南；`templates/` 包含 Hermes 配置、人设、Jev 原生插件、日报脚本和提示词、空账号列表、`.env.example`；`deploy/` 安装和启动独立实例；`tests/` 全部离线；`checks/` 放显式运行的真实 Hermes 与在线只读检查。

## 许可证

[MIT](LICENSE)。
