#!/bin/sh
# Check that the app in a Docker image starts on an older x86-64 CPU.
#
# Runs the image's Python on an emulated x86-64-v1 processor (QEMU's "qemu64"
# model: no SSE4.2/POPCNT), imports the app, and signs and verifies a token
# with PyJWT/cryptography (only loaded when Cloudflare Access sign-in is on,
# so importing the app alone wouldn't exercise them). Fails if any dependency needs
# a newer CPU, as numpy did when PyMuPDF 2.0 pulled it in and the image stopped
# starting on servers without x86-64-v2. A broken image can hang under
# emulation rather than crash, so the run is time-limited.
#
#   deploy/check-old-cpu.sh IMAGE            e.g. deploy/check-old-cpu.sh tcgnecro:test
#   CHECK_TIMEOUT=120 deploy/check-old-cpu.sh IMAGE
set -eu
image="${1:?usage: $0 IMAGE}"
limit="${CHECK_TIMEOUT:-300}"
dir=$(mktemp -d)
trap 'rm -rf "$dir"' EXIT

# A statically linked qemu-x86_64 from Debian, so it can run inside the image.
docker run --rm -v "$dir:/out" debian:stable-slim sh -c \
    'apt-get update -qq >/dev/null && apt-get install -y -qq qemu-user-static >/dev/null 2>&1;
     cp "$(command -v qemu-x86_64-static || command -v qemu-x86_64)" /out/qemu'

check='
import time, jwt
from cryptography.hazmat.primitives.asymmetric import rsa
import tcgnecro.web, tcgnecro.cli, tcgnecro.auth
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
token = jwt.encode({"aud": "x", "exp": int(time.time()) + 60}, key, algorithm="RS256")
jwt.decode(token, key.public_key(), algorithms=["RS256"], audience="x")
print("starts on an x86-64-v1 CPU: ok")'

if timeout "$limit" docker run --rm -v "$dir:/q:ro" --entrypoint /q/qemu "$image" -cpu qemu64 \
        /usr/local/bin/python -c "$check"; then
    exit 0
else
    status=$?
    [ "$status" = 124 ] && echo "No answer after ${limit}s on the emulated x86-64-v1 CPU." >&2
    echo "$image does not start on an x86-64-v1 CPU. A dependency probably needs a newer CPU;" \
         "compare 'pip list' with the previous image." >&2
    exit 1
fi
