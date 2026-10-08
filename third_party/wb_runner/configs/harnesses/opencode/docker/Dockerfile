# OpenCode harness mount image. The CLI version is supplied by harness YAML build args.
#
# Differs from the claude-code / codebuddy-code mounts in one structural way:
# OpenCode's published npm package (`opencode-ai`) is a thin installer that pulls a
# platform-specific binary at postinstall, and the CLI itself runs under **bun**,
# not node. So this image installs the package with its postinstall intact and
# ships whatever runtime landed alongside it, then writes a launcher shim that
# execs the resolved entrypoint by ABSOLUTE path under the mount — so nothing has
# to be on the container's PATH except the shim itself.

ARG OPENCODE_VERSION

FROM node:20-slim AS build
ARG OPENCODE_VERSION
ARG MOUNT_PATH=/opt/opencode
ENV MOUNT_PATH=${MOUNT_PATH}
RUN test -n "${OPENCODE_VERSION}"
# unzip/curl are needed by opencode-ai's postinstall (it fetches + unpacks the
# platform binary); ca-certificates for the TLS fetch.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g "opencode-ai@${OPENCODE_VERSION}"

# Bake ripgrep into the mount. OpenCode's glob/grep tools shell out to `rg`, and
# file/ripgrep.ts resolves it as: which("rg") on PATH FIRST, else download from
# github.com/BurntSushi/ripgrep at RUN TIME into ~/.cache/opencode/bin.
# That download cannot work in a bench rollout — task containers under Harbor's
# egress-control sidecar reach only host.docker.internal — and the failure mode is
# not a clean error: the fetch returns an error page, the archive "did not contain
# executable", and every glob/grep call fails mid-task. Shipping the binary here
# means `which("rg")` hits on the first try and no network is ever touched.
ARG RIPGREP_VERSION=14.1.1
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) rgtriple="x86_64-unknown-linux-musl" ;; \
      arm64) rgtriple="aarch64-unknown-linux-gnu" ;; \
      *) echo "unsupported arch for ripgrep: $arch" >&2; exit 1 ;; \
    esac; \
    url="https://github.com/BurntSushi/ripgrep/releases/download/${RIPGREP_VERSION}/ripgrep-${RIPGREP_VERSION}-${rgtriple}.tar.gz"; \
    curl -fsSL "$url" -o /tmp/rg.tar.gz; \
    tar -xzf /tmp/rg.tar.gz -C /tmp; \
    install -m 0755 "/tmp/ripgrep-${RIPGREP_VERSION}-${rgtriple}/rg" /usr/local/bin/rg; \
    rm -rf /tmp/rg.tar.gz "/tmp/ripgrep-${RIPGREP_VERSION}-${rgtriple}"; \
    rg --version

# Resolve the real entrypoint from the installed package and write the shim.
# opencode-ai ships either a native binary (bin/opencode[.exe]) or a JS entry; the
# shim handles both — exec the binary directly when it is executable, else run it
# through the bundled node. Fail the BUILD (not the run) if neither is found, so a
# broken mount image can never reach a rollout.
RUN set -eux; \
    mkdir -p /out/bin /out/lib; \
    cp -r /usr/local/lib/node_modules /out/lib/node_modules; \
    cp /usr/local/bin/node /out/bin/node; \
    cp /usr/local/bin/rg /out/bin/rg; \
    node -e ' \
      const fs = require("fs"), path = require("path"); \
      const root = "/usr/local/lib/node_modules/opencode-ai"; \
      const pkg = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8")); \
      const rawBin = pkg.bin; \
      const entry = typeof rawBin === "string" ? rawBin : (rawBin && (rawBin.opencode || rawBin["opencode-ai"])); \
      if (!entry) throw new Error("Cannot find opencode bin entry in opencode-ai package.json"); \
      const mountPath = process.env.MOUNT_PATH || "/opt/opencode"; \
      const rel = entry.replace(/^\.\//, ""); \
      const target = path.posix.join(mountPath, "lib/node_modules/opencode-ai", rel); \
      const nodeBin = path.posix.join(mountPath, "bin/node"); \
      const localEntry = path.join(root, rel); \
      if (!fs.existsSync(localEntry)) throw new Error("resolved entry missing: " + localEntry); \
      const isElf = (() => { try { const fd = fs.openSync(localEntry, "r"); const b = Buffer.alloc(4); \
        fs.readSync(fd, b, 0, 4, 0); fs.closeSync(fd); return b[0] === 0x7f && b.toString("latin1", 1, 4) === "ELF"; \
      } catch { return false; } })(); \
      const cmd = isElf ? `exec "${target}" "$@"` : `exec "${nodeBin}" "${target}" "$@"`; \
      fs.writeFileSync("/out/bin/opencode", `#!/usr/bin/env bash\n${cmd}\n`, { mode: 0o755 }); \
      console.error("shim -> " + (isElf ? "native " : "node ") + target); \
    '

FROM scratch
COPY --from=build /out/bin/ /bin/
COPY --from=build /out/lib/ /lib/
