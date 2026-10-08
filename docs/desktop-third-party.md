# 桌面版第三方组件

ShirOS 安装包包含 Python、FastAPI、SQLAlchemy、Alembic、Pydantic、psycopg、Pillow、pywebview、pythonnet、PostgreSQL、pgvector 及其运行依赖。PyInstaller 负责冻结程序，Inno Setup 负责编译安装包。

PostgreSQL 和 pgvector 使用随构建输入提供的 conda-forge Windows x64 运行时。分发时保留运行时 `share` 中的版权和许可证文件及 Python 包内的许可证；本项目没有取得任何第三方商标授权。WebView2 由 Microsoft 提供，属于系统前提，不随本安装包复制用户浏览器配置。

组件上游：

- PostgreSQL：https://www.postgresql.org/about/licence/
- pgvector：https://github.com/pgvector/pgvector
- Python：https://docs.python.org/3/license.html
- pywebview：https://github.com/r0x0r/pywebview
- PyInstaller：https://pyinstaller.org/
- Inno Setup：https://jrsoftware.org/

构建者应随实际依赖版本检查许可证及再分发条件；安装包不包含数据库集群、用户文件、登录令牌或私人记忆。
