// Streaming frame decoder: plain C with no Arduino dependency, so it can be tested on a PC.
#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Reads up to max bytes into buf. Returns the count read, or 0 or less on timeout or error.
typedef int (*fc_read_fn)(void *ctx, uint8_t *buf, int max);
// Receives decoded pixel bytes in order.
typedef void (*fc_write_fn)(void *ctx, const uint8_t *buf, int len);

enum {
  FC_OK = 0,
  FC_ERR_READ = -1,      // input ended early or timed out
  FC_ERR_DATA = -2,      // corrupt or unsupported compressed data
  FC_ERR_WINDOW = -3,    // compressor window larger than the decoder's dictionary
  FC_ERR_SIZE = -4,      // decoded size differs from the expected size
};

// Copies in_len raw bytes from rd to wr.
int fc_copy_stream(fc_read_fn rd, void *rctx, uint32_t in_len, fc_write_fn wr, void *wctx);

// Inflates an in_len byte zlib stream into exactly out_len bytes. dict must hold at least the
// compressor's window (send with wbits <= log2(dict_size)).
int fc_inflate_stream(fc_read_fn rd, void *rctx, uint32_t in_len, fc_write_fn wr, void *wctx,
                      uint32_t out_len, uint8_t *dict, unsigned dict_size);

#ifdef __cplusplus
}
#endif
