# Reuse the GPU-tested runtime without reinstalling or changing the model stack.
FROM ghcr.io/nwtia/nwtia-runpod-openjutsu-v3@sha256:9f99f359d54ce900eb95af3dcb74818d5c4dfbd7ef3c3922c37d1062a313e230
RUN cp /opt/nwtia/openjutsu/bootstrap.py /opt/nwtia/openjutsu/bootstrap.base.py
COPY bootstrap.py /opt/nwtia/openjutsu/bootstrap.py
COPY test_web_access.py /opt/nwtia/openjutsu/test_web_access.py
COPY validate_web_runtime.py /opt/nwtia/openjutsu/validate_web_runtime.py
RUN python3 /opt/nwtia/openjutsu/validate_web_runtime.py \
    && NWTIA_BOOTSTRAP=/opt/nwtia/openjutsu/bootstrap.py /opt/nwtia/openjutsu-runtime/venv/bin/python /opt/nwtia/openjutsu/test_web_access.py \
    && python3 /opt/nwtia/openjutsu/validate_install.py
LABEL org.opencontainers.image.title="NWTIA OpenJutsu V3 - browser access" \
      org.opencontainers.image.description="Private opening link; no SSH setup, streaming uploads and progress"
EXPOSE 8080
ENTRYPOINT ["python3", "-u", "/opt/nwtia/openjutsu/bootstrap.py"]
