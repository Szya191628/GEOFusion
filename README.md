# GEO Fusion v1.0 本地融合版

已安装：`D:\360download\GEOFusion`。统一入口：**http://127.0.0.1:8090/**；原 3009 地址自动转入。独立使用，不需要 AnswerField。

## 使用

双击 `start.cmd` 启动；`stop.cmd` 停止；`restart.cmd` 重启。服务以隐藏后台进程运行，日志位于 logs。启动器只停止属于本项目的服务，不抢占其他项目端口。机器重启后需重新启动；没有配置开机自启。

1. 在「技术审计」输入公开网站，检查 SEO/AEO/GEO 等技术信号；也可粘贴 HTML 快照。在线审计仅获取 HTTP HTML，不执行 JavaScript，每次最多 20 页、65 请求、12MB 总读取预算；失败/受限请求明确列出，部分失败时页面不展示技术评分。
2. 在「关键词规划」填写 1–8 个业务种子词，调用 GEORank 实际八维拓词逻辑；没有可用模型时明确显示规则模板结果，不冒充模型生成。建议分数不是搜索量或真实排名。
3. 在「连接与设置」配置自己的兼容 OpenAI 模型地址、名称与密钥；可测试连接。配置后可生成内容草稿和 30/60/90 天计划。内容与计划是本服务新编写的服务端工作流；不宣称上游已提供专用生成 API。模型生成会产生服务商费用，页面内容与证据会发给你配置的服务商。
4. 在「机器文件」填写已核实资料，生成 llms.txt、llms-full.txt、facts.json。默认不授予内容复用许可，人工确认有权授权后才更改许可。
5. 诊断结果可传入优化计划。配置可信站点源码目录后，审计中四类机械问题可定位源码并生成 Diff，绑定审计结果 hash 和源码 hash。低置信或不支持的框架可能只显示原因；不承诺所有问题均可修复。
6. 在任务详情核实草稿、记录批准/退回及备注，下载 ZIP。审核不会执行补丁或发布网站。模型输出中的事实、引用与建议仍需人工核实。

任务、输入、结果和审核记录保存在 `data/fusion.sqlite3`；运行中采用 WAL，备份时停止服务后复制 data 文件夹。中断中的任务在重启后标记失败，可重新运行。取消会阻止结果保存，但已发出的单次网络/模型调用可能持续到超时，不能保证撤销服务商计费。

## 已验证

- 原有与新增测试合计 **30 passed、1 dependency deprecation warning**。
- 浏览器实际创建关键词任务，获得 GEORank 八维 80 词的模板结果，并显示降级标记。
- 实际 HTTPS 在线审计 example.com 完成；返回技术问题和覆盖信息，不是模拟报告。
- 实际 HTTP 链路完成机器文件生成 → 批准 → ZIP 导出，含三种文件及任务证据。
- Docker 镜像 `geofusion:1.0` 构建成功；非 root、根目录只读的临时容器验证前端、浏览器会话、设置写入和草稿任务完成，验证容器已移除。
- 模型缺失、模型模拟成功/失败、鉴权、CSRF 头、Host 校验、DNS 内网拦截、任务取消不提交、审核版本冲突、持久化和源码不变性均有测试。

当前**未配置真实模型**，未完成真实模型的内容/计划生成验收。测试中的模型响应明确使用测试替身，不计为真实调用成功。上游 OmniRank 原生 Windows 全套测试仍有 11 个失败（Bash/权限、换行字节、symlink）；本服务限制只读并额外拒绝符号链接，单个静态源码 Diff 流程验证通过，不代表已解决全部上游失败。

## 部署与边界

Windows 重新安装：解压后运行 `install.ps1`（需要 uv），安装锁定依赖与包内 OmniRank 源码，再用 start.cmd。主服务不依赖另行部署 GEORank 全栈。

Docker：在本目录执行 `docker compose up --build -d`。默认只绑定本机 8090，与原生运行二选一。持久化数据与设置在命名卷 fusion-data；配置源码目录时需额外只读挂载实际代码，容器看不到 Windows 原始路径。容器的本机会话兼容开关仅配合 localhost 端口发布使用，不要将此单用户版本直接公开到互联网。

本版是**本机单用户完整工作流**，没有多租户 SaaS、客户站点自动发布、真实 AI 排名/引用追踪、AnswerField 监测指标或营销后台。依赖服务商的功能需要用户配置有效凭证。HTTP 大小/时间限制以资源读取与请求数控制为主，不是高并发公网服务的安全承诺。

本机代理如将域名解析为 198.18.0.0/15 虚拟地址，在线审计会通过 [Google DNS over HTTPS JSON API](https://developers.google.com/speed/public-dns/docs/doh/json) 查询公开域名真实 A 记录，再固定连接经验证的公网 IP；不会关闭 TLS 检查。正常 DNS 不触发此回退。域名会在回退时发送给 Google Public DNS；不会发送客户网页、模型密钥或本机文件。私有、元数据、回环地址及跨主机抓取始终拒绝。

## 来源与许可

- GEORank：`424a0cf92b37ad63c94ae9dc6f39745189ab7c94`，Apache-2.0。vendor/georank 保留原许可；keyword_expansion.py 仅替换 ai_client 导入，并在文件头标明变更。对应原始文件位于 upstream/GEORank/backend/app/services/keyword_expansion.py。未引入专家资料、封面和品牌资产。
- OmniRank：`b2d572ad646835e1c4d927fe522c58fbb51af9f9`，代码 MIT；文档与内容 CC BY 4.0，署名 S M Moshiur Rahman / https://ticonsys.com 。保留 upstream/OmniRank/LICENSE 和 LICENSE-CONTENT。
- 所有自动生成的内容和修改保持草稿 / Diff；此发行版没有客户网站写入接口。
