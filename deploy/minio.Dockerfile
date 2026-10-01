# The MinIO Community image is no longer published to Docker Hub. Build its final
# source-only release for this disposable local Phase 1 stack. This is not a
# production image; see G-28 and docs/dependency-review.md.
FROM golang:1.24-alpine AS build
RUN GOBIN=/out go install github.com/minio/minio@RELEASE.2025-10-15T17-29-55Z

FROM alpine:3.22
RUN apk add --no-cache ca-certificates
COPY --from=busybox:1.37.0-musl /bin/busybox /busybox
COPY --from=build /out/minio /usr/bin/minio
ENTRYPOINT ["minio"]
