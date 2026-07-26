# VOACAP web service - runs the engine via voacapl (github.com/jawatson/voacapl),
# a native Linux Fortran port, NOT the original Win32 binaries under Wine.
# Wine hit a genuine internal livelock with the original Salford-compiled
# .exe (confirmed via strace: two threads busy-waiting on the same message
# exchange, tens of thousands of times/sec, never resolving) - voacapl reads
# the same card-deck input format and runs natively in ~2ms.

FROM debian:bookworm-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        gfortran gcc g++ make automake autoconf libtool git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN git clone --depth 1 https://github.com/jawatson/voacapl.git

RUN cd voacapl \
    && autoreconf -fi \
    && ./configure \
    && make \
    && make install

# Pinned to an exact patch version (not floating "3.13" or "3.13-slim") so
# the interpreter doesn't drift out from under the DNS record this is
# hosted under - bump this deliberately, not via an unpinned rebuild.
FROM python:3.13.13-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        libgfortran5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/bin/voacapl /usr/local/bin/voacapl
COPY --from=builder /usr/local/share/voacapl /usr/local/share/voacapl

# Assemble a working itshfbc data directory the same way makeitshfbc does,
# but under a fixed system path instead of a per-user $HOME (this is a
# single-purpose service container, not a multi-user desktop install).
RUN mkdir -p /opt/itshfbc \
    && for d in areadata area_inv run antennas; do \
        mkdir -p /opt/itshfbc/$d && cp -R /usr/local/share/voacapl/itshfbc/$d/* /opt/itshfbc/$d/; \
    done \
    && mkdir -p /opt/itshfbc/database \
    && for f in cirafp.911 colors.dat colors.win version.win north_pole.txt version.w32 voacap.def; do \
        cp /usr/local/share/voacapl/itshfbc/database/$f /opt/itshfbc/database/; \
    done \
    && for d in coeffs geocity geonatio geostate; do \
        ln -s /usr/local/share/voacapl/itshfbc/$d /opt/itshfbc/$d; \
    done

# App code
COPY app/ /opt/app/
RUN pip3 install --no-cache-dir -r /opt/app/requirements.txt

ENV ITSHFBC_DIR=/opt/itshfbc \
    VOACAPL_BIN=/usr/local/bin/voacapl

WORKDIR /opt/app
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
