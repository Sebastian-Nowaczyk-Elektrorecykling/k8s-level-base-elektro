# Keep the Kubernetes patch version aligned with the golden k3s installation.
ARG NODE_IMAGE
FROM ${NODE_IMAGE}
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       open-iscsi nfs-common cryptsetup dmsetup xfsprogs e2fsprogs kmod \
    && rm -rf /var/lib/apt/lists/*
