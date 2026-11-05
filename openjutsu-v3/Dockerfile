# syntax=docker/dockerfile:1
# Independent COPY layers reuse the tested GPU runtime without downloading/rebuilding it.
FROM ghcr.io/nwtia/nwtia-runpod-openjutsu-v3@sha256:be5d351b452e467a42bef435772493b12d4b54560d0c145f75523684f33f43f0
COPY --link bootstrap.py gateway_upload.py /opt/nwtia/openjutsu/
COPY --link overlay.zip /opt/nwtia/openjutsu/overlay.zip
COPY --link installed.upload.json /opt/nwtia/openjutsu-runtime/installed.json
COPY --link app-overlay/ /opt/nwtia/openjutsu-runtime/app/
COPY --link frontend-build/.next/ /opt/nwtia/openjutsu-runtime/app/.next/
LABEL org.opencontainers.image.title="NWTIA OpenJutsu V3 - browser access" \
      org.opencontainers.image.description="Private browser link; byte-preserving uploads up to 2 GiB in verified 8 MiB parts"
EXPOSE 8080
ENTRYPOINT ["python3", "-u", "/opt/nwtia/openjutsu/bootstrap.py"]
