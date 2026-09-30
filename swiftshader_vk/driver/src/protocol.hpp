// vkreplay: the child -> parent message protocol.
//
// A message is  u32 header_len | u64 blob_len | header (JSON text) | blob.
// The parent is the only reader and treats every byte as untrusted: sizes are
// bounded, the header must parse, and message types and op indices are checked
// against the parent's own reading of the case.
#pragma once

#include "common.hpp"

#include <string>

namespace proto {

constexpr uint32_t kMaxHeader = 16u << 20;     // 16 MiB of JSON per message
constexpr uint64_t kMaxBlob = 256ull << 20;    // 256 MiB of snapshot bytes per message

struct Message {
    json head;
    std::string blob;
};

// Write one message; returns false if the peer is gone.
bool write_msg(int fd, const json& head, const void* blob = nullptr, size_t blob_len = 0);

enum class ReadStatus { Ok, Eof, Timeout, Bad };
// Read one message, waiting at most until `deadline_ms` (CLOCK_MONOTONIC milliseconds; <0: forever).
ReadStatus read_msg(int fd, Message* out, int64_t deadline_ms, std::string* why);

int64_t now_ms();
double now_s();

}  // namespace proto
