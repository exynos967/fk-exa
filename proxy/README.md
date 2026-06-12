# Legacy Proxy Note

`proxy/` 的实现已经迁移出这个仓库。

当前目录刻意只保留这份 `README.md`，用于说明迁移关系，不再包含
`server.py`、`docker-compose.yml`、模板或运行所需文件。

如果你要部署正式对外可用的统一搜索控制台、MCP、Skill 和 Social / X
路由，请直接使用独立仓库：

- [MySearch-Proxy](https://github.com/skernelx/MySearch-Proxy)

## 当前推荐的正式入口

`MySearch-Proxy` 负责：

- 统一控制台
- `Firecrawl + Social / X` 聚合路由
- MCP Server
- 可直接安装给智能体使用的 Skill
- 官方接口与 compatible 网关双接入
- 面向 `Codex` / `Claude Code` 的最终接线方式

## 控制台预览

### 首屏

![MySearch Console Hero](../docs/screenshots/mysearch-console-hero.jpg)

### 工作台

![MySearch Console Workspaces](../docs/screenshots/mysearch-console-workspaces.jpg)

## 和 `tavily-key-generator` 的关系

当前仓库继续负责上游 provider 层：

- 注册 Firecrawl / Exa key
- 做真实可用性验证
- 可选上传到统一代理池
- 作为 `MySearch-Proxy` 的 Firecrawl / Exa key 来源

注册器上传时仍会带上 `service` 字段，例如：

```json
{
  "key": "fc-xxxx",
  "email": "fc-xxx@example.com",
  "service": "firecrawl"
}
```

所以：

- Firecrawl 上传会进入 Firecrawl 池
- Exa 上传会进入 Exa 池
- 服务端不需要再靠 key 前缀猜测服务

## 迁移建议

推荐直接部署 `MySearch-Proxy`：

```bash
docker pull skernelx/mysearch-proxy:latest
```

项目地址：

- [skernelx/MySearch-Proxy](https://github.com/skernelx/MySearch-Proxy)

如果你之前已经从这个仓库的旧 `proxy/` 部署过服务，更稳的迁移方式是：

1. 保留原来的数据卷目录
2. 改用 `skernelx/mysearch-proxy:latest`
3. 继续挂载到 `/app/data`
4. 在新仓库里按最新 README 补齐环境变量

## 兼容 API 摘要

`MySearch-Proxy` 仍然兼容这些重点接口：

- Firecrawl
  - `/firecrawl/*`
  - 示例：`POST /firecrawl/v2/scrape`
- Social / X
  - `POST /social/search`
  - `GET /social/health`

认证方式支持：

- `Authorization: Bearer YOUR_TOKEN`
- body 里传 `api_key`
