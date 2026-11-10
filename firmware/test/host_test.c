// Runs the frame decoder on a PC. Build and run with firmware/test/run_host_test.sh.
// Usage: host_test <compressed.zlib> <expected.raw> <dict_size> <seed>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../src/frame_codec.h"

typedef struct {
  const uint8_t *data;
  size_t len, pos;
} source_t;

typedef struct {
  uint8_t *data;
  size_t len, pos;
} sink_t;

// Hands out random chunk sizes to imitate TCP segments arriving unevenly.
static int read_random(void *ctx, uint8_t *buf, int max) {
  source_t *s = ctx;
  if (s->pos >= s->len) return 0;
  int n = 1 + rand() % 1460;
  if (n > max) n = max;
  if ((size_t)n > s->len - s->pos) n = (int)(s->len - s->pos);
  memcpy(buf, s->data + s->pos, n);
  s->pos += n;
  return n;
}

static void write_sink(void *ctx, const uint8_t *buf, int len) {
  sink_t *s = ctx;
  if (s->pos + len <= s->len) memcpy(s->data + s->pos, buf, len);
  s->pos += len;
}

static uint8_t *load(const char *path, size_t *len) {
  FILE *f = fopen(path, "rb");
  if (!f) return NULL;
  fseek(f, 0, SEEK_END);
  *len = ftell(f);
  rewind(f);
  uint8_t *buf = malloc(*len);
  if (fread(buf, 1, *len, f) != *len) return NULL;
  fclose(f);
  return buf;
}

int main(int argc, char **argv) {
  if (argc != 5) return 2;
  size_t zlen, rawlen;
  uint8_t *z = load(argv[1], &zlen), *raw = load(argv[2], &rawlen);
  if (!z || !raw) return 2;
  unsigned dict_size = (unsigned)atoi(argv[3]);
  srand((unsigned)atoi(argv[4]));
  uint8_t *dict = malloc(dict_size);

  source_t src = {z, zlen, 0};
  sink_t out = {calloc(rawlen, 1), rawlen, 0};
  int rc = fc_inflate_stream(read_random, &src, (uint32_t)zlen, write_sink, &out, (uint32_t)rawlen, dict, dict_size);
  int same = out.pos == rawlen && memcmp(out.data, raw, rawlen) == 0;
  printf("inflate rc=%d bytes=%zu match=%s consumed=%zu/%zu\n", rc, out.pos, same ? "yes" : "NO", src.pos, zlen);

  source_t raw_src = {raw, rawlen, 0};
  sink_t copy = {calloc(rawlen, 1), rawlen, 0};
  int rc2 = fc_copy_stream(read_random, &raw_src, (uint32_t)rawlen, write_sink, &copy);
  int same2 = copy.pos == rawlen && memcmp(copy.data, raw, rawlen) == 0;
  printf("copy    rc=%d match=%s\n", rc2, same2 ? "yes" : "NO");
  return (rc == 0 && same && rc2 == 0 && same2) ? 0 : 1;
}
