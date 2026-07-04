# 2026 世界杯全赛程驾驶舱

页面从 `data/standings.json` 读取小组积分、全赛程、下一场比赛和淘汰赛晋级路径，并每 5 分钟检查一次新版本。GitHub Actions 每 15 分钟获取比赛数据，重新计算积分、比分、进球和赛事进度。

## 自动更新链路

```text
football-data.org 比赛接口
        ↓ 每 15 分钟
scripts/update_standings.py
        ↓ 校验并原子替换
data/standings.json
        ↓ 页面每 5 分钟读取
驾驶舱
```

更新器会把接口返回的比赛写入全赛程；其中 A–L 组且状态为 `FINISHED` 或 `AWARDED` 的比赛会进入小组积分计算。淘汰赛未知球队会显示为占位，不会阻断赛程更新；但小组积分里的未知球队、比赛数量回退或数据异常会让任务失败并保留最后一次有效数据。

## 首次启用

1. 在 [football-data.org](https://www.football-data.org/client/register) 注册并取得 API Token。
2. 将项目推送到 GitHub。
3. 打开仓库的 `Settings → Secrets and variables → Actions`。
4. 新建 Repository secret：

   - Name：`FOOTBALL_DATA_TOKEN`
   - Secret：你的 API Token

5. 打开 `Actions → Update World Cup standings → Run workflow`，手动执行一次。
6. 如使用 GitHub Pages，在 `Settings → Pages` 中选择从主分支根目录发布。

工作流文件位于 `.github/workflows/update-standings.yml`。计划任务按 UTC 执行，但 `*/15 * * * *` 表示全天每 15 分钟触发，不受时区影响。GitHub 计划任务可能有几分钟延迟。

## 本地运行

启动页面：

```bash
python3 -m http.server 8000
```

然后访问 <http://localhost:8000>。不要直接双击 `index.html`，浏览器通常会阻止 `file://` 页面读取 JSON。

手动更新：

```bash
FOOTBALL_DATA_TOKEN="你的令牌" python3 scripts/update_standings.py
```

只验证、不写文件：

```bash
FOOTBALL_DATA_TOKEN="你的令牌" python3 scripts/update_standings.py --dry-run
```

运行测试：

```bash
python3 -m unittest discover -s tests -v
```

## 数据与展示规则

- 数据源：football-data.org 的 `WC` 比赛接口，赛季为 `2026`
- 积分：胜 3 分、平 1 分、负 0 分
- 排序：积分、总净胜球、总进球、相关球队相互战绩
- 赛程：按阶段与北京时间排序，支持小组赛、32 强、16 强、1/4 决赛、半决赛、季军赛和决赛
- 下一场：从未完赛比赛中选取时间最早的一场
- 接口未提供公平竞赛积分等最终判定信息时，以种子文件中的原顺序保持稳定
- 最终名次应以 FIFA 官方公布结果为准

API Token 只存放在 GitHub Actions Secret 或本地环境变量中，不会发送到浏览器或写入仓库。
