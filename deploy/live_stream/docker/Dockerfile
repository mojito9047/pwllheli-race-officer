# MediaMTX + ffmpeg + python for the branded on-demand source.
# The -ffmpeg tag is Alpine-based and includes ffmpeg; we add python3 plus numpy +
# Pillow (used by the optional ODM start-line overlay) so runOnDemand can call
# relay_branded_source.py. The config and scripts are mounted at runtime by
# docker-compose (no rebuild needed to tweak them).
FROM bluenviron/mediamtx:latest-ffmpeg
RUN apk add --no-cache python3 py3-numpy py3-pillow
