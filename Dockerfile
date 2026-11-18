# syntax=docker/dockerfile:1
#
# GTD4Teams —— 单端口 HTTP 服务，**零第三方依赖**（纯 Python 标准库）。
#
# 因此这个镜像不需要 pip install，也没有 requirements.txt：
# 构建过程只是把代码放进一个干净的 Python 运行时，不联网装包、不编译任何东西。
# 这一点对"随处可运行"很关键 —— 服务器上 pip 能不能出网、有没有编译器，都不影响构建。

FROM python:3.13-slim

# PYTHONDONTWRITEBYTECODE：不生成 .pyc，这是能以**只读根文件系统**运行的前提
#                         （见 docker-compose.yml 的 read_only）。
# PYTHONUNBUFFERED：      stdout 不缓冲，docker logs 能立刻看到输出而不是攒批。
# GTD_HOST=0.0.0.0：      容器内必须绑全零地址，否则端口映射根本进不来。
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GTD_HOST=0.0.0.0 \
    GTD_PORT=8787 \
    GTD_DATA_DIR=/data

WORKDIR /app

# 白名单式 COPY：只把运行期真正需要的部分放进镜像。
# 测试、冒烟脚本、项目笔记、部署脚本都不进去 ——
# 镜像里不存在的东西，就不存在"被静态服务误暴露"的风险。
COPY index.html ./
COPY server.py ./
COPY pyapp/ ./pyapp/
COPY js/ ./js/
COPY css/ ./css/

# ---- 构建即自检：让"构建成功"本身等价于一条冒烟测试 ----
# 放在 COPY 之后、ARG 之前，位置是有意的：
#   · 在 COPY 之后 —— 缺文件、模块名写错、循环导入、语法错误都会在这里让构建失败，
#     而不是等容器起来后在运行时报 500。这比"能 COPY 进去"强得多。
#   · 在 ARG GTD_BUILD 之前 —— 这一层不随构建标识变化，重复构建能直接命中缓存。
RUN set -eux; \
    python -c "import pyapp.config, pyapp.db, pyapp.httpd, pyapp.llm, pyapp.policy, pyapp.security, pyapp.store"; \
    python server.py --check --data-dir /tmp/gtd-buildcheck >/dev/null; \
    rm -rf /tmp/gtd-buildcheck

# ---- 构建标识 ----
# 发布脚本用 --build-arg GTD_BUILD=<时间戳-短提交> 注入，运行时由 /api/health 回传。
# 这是蓝绿切换的核对依据：新槽位回传的 build 必须等于本次构建的标识，
# 否则说明切换命中的是旧容器（旧镜像同样健康、端口同样是通的）。
ARG GTD_BUILD=dev
ENV GTD_BUILD=${GTD_BUILD}

# 非 root 运行；预建数据目录并交出属主。
# uid 固定为 10001 是有意的：改用 bind mount 挂宿主机目录时，
# 文档里那条 chown 命令需要一个确定的数字。
RUN set -eux; \
    useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin gtd; \
    mkdir -p /data; \
    chown -R gtd:gtd /data /app

USER gtd

# 数据落在卷里，容器本身可以随时销毁重建
VOLUME ["/data"]
EXPOSE 8787

# 健康检查直接用 Python 标准库发请求，**不装 curl** —— 少一层依赖，也少一个攻击面。
# /api/health 是免鉴权端点，正好用来判断"服务是否真的在干活"而不只是"进程还在"。
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('GTD_PORT','8787')+'/api/health',timeout=3)"

# 不加 shell 包装：让 PID 1 直接就是 python，SIGTERM 才能送达进程本身。
# server.py 里已接上优雅关闭（停服务时先把在途请求与 SQLite 写事务收尾）。
CMD ["python", "server.py"]
