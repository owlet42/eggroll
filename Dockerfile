FROM maven:3.8-jdk-8 as builder

WORKDIR /data/projects/fate/eggroll

COPY ./ /data/projects/fate/eggroll/

RUN cd /data/projects/fate/eggroll/deploy && bash auto-packaging.sh

# 
RUN mkdir /data/projects/fate/eggroll/eggroll && tar -xzf eggroll.tar.gz -C /data/projects/fate/eggroll/eggroll

RUN ls

FROM centos:centos7 as base

USER root

# install dependencies
RUN set -eux && \
    rpm --rebuilddb && \
    rpm --import /etc/pki/rpm-gpg/RPM* && \
    yum -y install gcc gcc-c++ make openssl-devel supervisor gmp-devel mpfr-devel libmpc-devel \
    libaio numactl autoconf automake libtool libffi-devel snappy snappy-devel zlib zlib-devel bzip2 bzip2-devel lz4-devel libasan lsof xz-devel && \
    yum clean all

# install python3.8
RUN curl -o Python-3.8.13.tar.xz https://www.python.org/ftp/python/3.8.13/Python-3.8.13.tar.xz && \
    tar -xvf Python-3.8.13.tar.xz && \
    cd Python-3.8.13 && \
    ./configure --prefix=/opt/python3 && \
    make altinstall && \
    ln -s /opt/python3/bin/python3.8 /usr/local/bin/python3.8 && \
    ln -s /usr/local/bin/python3.8 /usr/local/bin/python3 && \
    ln -s /usr/local/bin/python3 /usr/local/bin/python && \
    ln -s /opt/python3/bin/pip3.8 /usr/bin/pip3.8 && \
    ln -s /usr/bin/pip3.8 /usr/bin/pip3 && \
    ln -s /usr/bin/pip3 /usr/bin/pip && \
    cd .. && \
    rm Python-3.8.13.tar.xz && \
    rm -rf Python-3.8.13

WORKDIR /data/projects/fate

ENV VIRTUAL_ENV=/data/projects/fate/common/python/venv

RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# install jdk
RUN set -eux; \
    rpm --rebuilddb; \
    rpm --import /etc/pki/rpm-gpg/RPM*; \
    yum install -y which strace java-1.8.0-openjdk ; \
    yum clean all;

FROM base

WORKDIR /data/projects/fate/eggroll

# COPY code
COPY --from=builder /data/projects/fate/eggroll/eggroll/ /data/projects/fate/eggroll/
COPY --from=builder /data/projects/fate/eggroll/requirements.txt /data/projects/fate/eggroll/

# expose ports
EXPOSE 4670 9394 9360

ENV PYTHONPATH=/data/projects/fate/fate:/data/projects/fate/fate_flow/python:/data/projects/fate/fate_client/python:/data/projects/fate/eggroll/python
ENV EGGROLL_HOME=/data/projects/fate/eggroll/

RUN sed -i "s/python-rocksdb==0.7.0/# python-rocksdb==0.7.0/g" requirements.txt && \
    python3 -m pip install --upgrade pip && python3 -m pip install -r requirements.txt

ENV TINI_VERSION v0.18.0
ADD https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini /tini
RUN chmod +x /tini
ENTRYPOINT ["/tini", "--"]
