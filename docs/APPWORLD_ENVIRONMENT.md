# AppWorld 真实环境验证记录

验证日期：2026-10-24（Asia/Shanghai）。本记录来自实际 WSL2 环境安装及两次独立执行的无模型适配检查，报告不包含任务指令、账户内容、模型凭据、gold solution 或私有评分断言。

## 环境与安装

实际环境为 Ubuntu 24.04 / WSL2，Python **3.12.3**，kernel `6.18.33.2-microsoft-standard-WSL2`；固定 AppWorld **0.1.3.post1**、GEPA **0.1.4**。依赖安装在 Linux 的独立 `~/.venvs/acl-appworld-py312`，没有修改 Windows `.venv`。数据保存在 Linux 本地 `~/acl-appworld-data`，不在 Git 工作区内。

在仓库的 WSL 工作目录执行的流程如下。数据下载和解包均返回成功，随后 `pip check` 输出 `No broken requirements found.`。官方固定版本安装入口核对自 [AppWorld 0.1.3.post1 发布包](https://pypi.org/project/appworld/0.1.3.post1/)的 `appworld/cli.py`。

```bash
python3 -m venv "$HOME/.venvs/acl-appworld-py312"
"$HOME/.venvs/acl-appworld-py312/bin/python" -m pip install appworld==0.1.3.post1 gepa==0.1.4 -e .
"$HOME/.venvs/acl-appworld-py312/bin/python" -m appworld.cli install
"$HOME/.venvs/acl-appworld-py312/bin/python" -m appworld.cli download data --root "$HOME/acl-appworld-data"
"$HOME/.venvs/acl-appworld-py312/bin/python" -m pip check
"$HOME/.venvs/acl-appworld-py312/bin/python" scripts/validate_appworld_adapter.py --appworld-root "$HOME/acl-appworld-data" --out runs/local-live/appworld-environment-002.json
```

第二次验证已占用上面报告名；复跑时使用新的输出名。脚本拒绝覆盖已有报告，官方实验输出名称也加入随机运行标识，避免覆盖先前状态。安装后测得任务数据目录约 193 MiB、独立虚拟环境约 224 MiB；这些是本机观察值，后续实验输出会另占磁盘。

## 已通过的真实检查

第二次检查 UTC 时间为 `2026-10-23T16:56:09.381994+00:00`，对应北京时间 2026-10-24 00:56:09。使用官方 train 任务 ID `82e2fac_1`，执行 seed 101，两次 fresh world；脚本返回码 0，`completed=true`，约 11.22 秒，**模型调用次数 0**。

| 检查 | 结果 |
| --- | --- |
| 公开 API 文档执行返回非空响应 | 通过 |
| supervisor 完成调用实际改变公开任务状态 | 通过 |
| 调用官方终态 evaluator | 通过 |
| 新世界恢复初始公开状态 | 通过 |
| 新世界恢复相同初始官方分数 | 通过，初始分数为 0 |
| actor 直接读取宿主文件被策略拒绝 | 通过 |
| 清单任务的官方 split 成员身份 | 通过 |

第一版脚本也完成了一次同类检查，约 13.14 秒；第二次增加了依赖锁、split 哈希和证据防覆盖。报告路径为被忽略的 `runs/local-live/appworld-environment-001.json`、`runs/local-live/appworld-environment-002.json`，本页只公开聚合观察。后者的验证脚本 SHA-256 为 `0902726a3fe106b755cfcecca93f635dd41f24bcadffd81c296ce8352cea9672`。

这验证了真实运行、状态重置、公共 API 和官方评分的接通，没有证明任何模型解决了任务，也没有做完整任务的正确/错误解答对照。初始得分 0 是环境检查的观察值，不是模型能力结果。模型实验仍需单独记录固定模型身份、manifest、实际用量和冻结 artifacts。

AST 策略及独立 worker 限制 actor 的访问路径，worker 不继承 API 凭据并禁用自动读取 `.env`；这些不是对任意恶意 Python 的操作系统沙箱承诺。正式更大规模运行可进一步置于隔离容器，不能把测试通过扩张成未验证的安全保证。

## 当前数据成员快照

本次官方命令得到的 task 数量如下，哈希针对原样 `data/datasets/<split>.txt` 文件。只读取 test split 的 ID 成员文件以验证身份与版本，没有执行 test 任务或读取其任务内容、解法和私有评价断言。

| 官方 split | task 数 | 成员文件 SHA-256 |
| --- | ---: | --- |
| train | 90 | `93d9fe71e7a2e3b7529803d4a20b604f4ebf5ae806f321081140238068189d37` |
| dev | 57 | `9fa976589300ea8905708257144d801d1604b06d85fb0181e381df8a3ba85001` |
| test_normal | 168 | `c3af41497b6f2f0860a2ff8c09b335dca527e2cf48e59b4aabdb301b6b68db8f` |
| test_challenge | 417 | `3c32b481042ac97f7d3477d53f5d196245c885c438d652944edc8a9a28e0f028` |

当前 train 为 90、dev 为 57，与论文历史数量 105/60 不同。实验分母必须以此下载快照及最终不可变 manifest 为准，不以论文数量覆盖实际数据。成员文件哈希只固定 split ID 清单，不能替代全体任务数据库的内容哈希；正式研究仍需在许可允许范围内保存数据版本与工件校验。

默认 `prepare-appworld` 当前产生 source 72、内部 dev 18、support 19、query 38；这是 within-generator 管线验收清单，不是跨 generator 的严格研究清单。严格 pilot 需使用独立宽领域 manifest，启用 `group_disjoint_support_query=true`，并保持 support/query generator 互斥。

## 实际依赖版本锁记录

以下来自本次运行的安装分发元数据，包含传递依赖与 pip。项目本身为当前 checkout 的 editable `agent-construction-lab==0.3.0`，因此不以 PyPI 版本替代代码提交。运行代码来自基线 `adc431d9f445d36f0f3be631ef72582bdcca7c86` 的工作区，包含本轮尚未提交的修改；最终归档应记录合并后的提交。

这是精确版本记录，不包含 wheel 文件哈希，不声称仅凭版本号可保证所有平台的二进制一致。复现时使用同类 Linux/Python 3.12 环境，按下列版本建立约束后运行相同验证脚本。

```text
Faker==24.14.1
PyJWT==2.15.1
PyYAML==6.0.3
Pygments==2.21.0
SQLAlchemy==1.4.54
SQLAlchemy-Utils==0.41.2
anyio==4.15.1
appworld==0.1.3.post1
asttokens==3.0.2
attrs==26.1.0
certifi==2026.10.23
cffi==2.1.1
charset-normalizer==3.5.2
click==8.5.0
cryptography==39.0.2
decorator==5.3.1
dnspython==2.9.0
email-validator==2.3.0
execnet==2.1.2
executing==2.3.0
fastapi==0.110.3
fastapi-login==1.10.3
freezegun==1.5.1
gepa==0.1.4
greenlet==3.5.6
h11==0.16.0
httpcore==1.0.9
httpx==0.27.2
idna==3.20
inflection==0.5.1
iniconfig==2.3.1
ipython==9.12.0
ipython_pygments_lexers==1.1.1
jedi==0.20.1
jsonref==1.1.0
libcst==1.9.0
markdown-it-py==4.2.0
matplotlib-inline==0.2.2
mdurl==0.1.2
munch==4.0.0
orjson==3.13.0
outcome==1.3.0.post0
packaging==26.3
parso==0.8.7
pendulum==3.3.0
pexpect==4.9.0
pip==24.0
pluggy==1.7.0
polyfactory==2.22.5
prompt_toolkit==3.0.53
psutil==5.9.8
ptyprocess==0.7.0
pure_eval==0.2.4
pycparser==3.12
pydantic==1.10.26
pytest==8.4.2
pytest-xdist==3.8.0
python-dateutil==3.0.0
python-dotenv==1.2.4
python-multipart==0.0.9
registrable==0.0.4
requests==2.34.2
rich==13.9.4
shellingham==1.5.4
six==1.17.0
sniffio==1.3.1
sortedcontainers==2.4.0
sqlalchemy2-stubs==0.0.2a38
sqlmodel==0.0.10
stack-data==0.6.3
starlette==0.37.2
tqdm==4.70.1
traitlets==5.16.1
trio==0.34.0
typer==0.16.1
typing_extensions==4.16.0
tzdata==2026.5
urllib3==2.8.0
uvicorn==0.27.1
uvloop==0.21.0
wcwidth==0.9.3
xxhash==3.8.2
```

