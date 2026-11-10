// Streaming frame decoder built on uzlib, which ships with the ESP8266 Arduino core.
#include "frame_codec.h"

#include <string.h>

#include "uzlib.h"

#define CHUNK 512

int fc_copy_stream(fc_read_fn rd, void *rctx, uint32_t in_len, fc_write_fn wr, void *wctx) {
  uint8_t buf[CHUNK];
  while (in_len) {
    int n = rd(rctx, buf, in_len < CHUNK ? (int)in_len : CHUNK);
    if (n <= 0) return FC_ERR_READ;
    wr(wctx, buf, n);
    in_len -= (uint32_t)n;
  }
  return FC_OK;
}

// uzlib asks for one byte at a time through source_read_cb. We refill a whole buffer on each call
// and point source/source_limit at it, so most bytes come straight from memory.
typedef struct {
  struct uzlib_uncomp d;  // must stay first: the callback casts back to this struct
  fc_read_fn rd;
  void *rctx;
  uint32_t remaining;
  int failed;
  uint8_t in[CHUNK];
} reader_t;

static int refill(struct uzlib_uncomp *d) {
  reader_t *r = (reader_t *)d;
  if (r->remaining == 0) return -1;
  int n = r->rd(r->rctx, r->in, r->remaining < CHUNK ? (int)r->remaining : CHUNK);
  if (n <= 0) {
    r->failed = 1;
    return -1;
  }
  r->remaining -= (uint32_t)n;
  d->source = r->in + 1;
  d->source_limit = r->in + n;
  return r->in[0];
}

int fc_inflate_stream(fc_read_fn rd, void *rctx, uint32_t in_len, fc_write_fn wr, void *wctx,
                      uint32_t out_len, uint8_t *dict, unsigned dict_size) {
  static reader_t r;  // about 1.7 KB; static to keep it off the small stack
  uint8_t out[CHUNK];
  memset(&r, 0, sizeof(r));
  r.rd = rd;
  r.rctx = rctx;
  r.remaining = in_len;

  uzlib_init();
  uzlib_uncompress_init(&r.d, dict, dict_size);
  r.d.source = NULL;
  r.d.source_limit = NULL;
  r.d.source_read_cb = refill;

  int window = uzlib_zlib_parse_header(&r.d);
  if (r.failed) return FC_ERR_READ;
  if (window < 0) return FC_ERR_DATA;
  if ((1u << (window + 8)) > dict_size) return FC_ERR_WINDOW;

  uint32_t written = 0;
  int res = TINF_OK;
  while (res == TINF_OK && written < out_len) {
    uint32_t want = out_len - written < CHUNK ? out_len - written : CHUNK;
    r.d.dest_start = r.d.dest = out;
    r.d.dest_limit = out + want;
    res = uzlib_uncompress_chksum(&r.d);
    int produced = (int)(r.d.dest - out);
    if (produced > 0) {
      wr(wctx, out, produced);
      written += (uint32_t)produced;
    }
  }
  // The stream ends with a 4-byte checksum; finish reading it if the pixels ended first.
  if (res == TINF_OK && written == out_len) {
    r.d.dest_start = r.d.dest = out;
    r.d.dest_limit = out;
    res = uzlib_uncompress_chksum(&r.d);
  }
  if (r.failed) return FC_ERR_READ;
  if (res != TINF_DONE) return FC_ERR_DATA;
  return written == out_len ? FC_OK : FC_ERR_SIZE;
}
