// vkreplay: the child -> parent message protocol (see protocol.hpp).
#include "protocol.hpp"

#include <cerrno>
#include <cstring>
#include <ctime>
#include <poll.h>
#include <unistd.h>

namespace proto {

int64_t now_ms() {
    timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

double now_s() {
    timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + ts.tv_nsec * 1e-9;
}

static bool write_all(int fd, const void* p, size_t n) {
    const char* c = static_cast<const char*>(p);
    while (n) {
        ssize_t w = ::write(fd, c, n);
        if (w < 0) {
            if (errno == EINTR) continue;
            return false;
        }
        c += w;
        n -= (size_t)w;
    }
    return true;
}

bool write_msg(int fd, const json& head, const void* blob, size_t blob_len) {
    std::string h = head.dump();
    uint32_t hl = (uint32_t)h.size();
    uint64_t bl = blob_len;
    char pre[12];
    std::memcpy(pre, &hl, 4);
    std::memcpy(pre + 4, &bl, 8);
    return write_all(fd, pre, 12) && write_all(fd, h.data(), h.size()) &&
           (blob_len == 0 || write_all(fd, blob, blob_len));
}

// Read exactly n bytes before the deadline.
static ReadStatus read_exact(int fd, char* p, size_t n, int64_t deadline_ms, bool* got_any) {
    while (n) {
        int wait = -1;
        if (deadline_ms >= 0) {
            int64_t left = deadline_ms - now_ms();
            if (left <= 0) return ReadStatus::Timeout;
            wait = (int)std::min<int64_t>(left, 1000000);
        }
        pollfd pfd{fd, POLLIN, 0};
        int pr = ::poll(&pfd, 1, wait);
        if (pr < 0) {
            if (errno == EINTR) continue;
            return ReadStatus::Bad;
        }
        if (pr == 0) continue;
        ssize_t r = ::read(fd, p, n);
        if (r < 0) {
            if (errno == EINTR || errno == EAGAIN) continue;
            return ReadStatus::Bad;
        }
        if (r == 0) return *got_any ? ReadStatus::Bad : ReadStatus::Eof;
        *got_any = true;
        p += r;
        n -= (size_t)r;
    }
    return ReadStatus::Ok;
}

ReadStatus read_msg(int fd, Message* out, int64_t deadline_ms, std::string* why) {
    char pre[12];
    bool any = false;
    ReadStatus s = read_exact(fd, pre, 12, deadline_ms, &any);
    if (s != ReadStatus::Ok) {
        if (s == ReadStatus::Bad) *why = "truncated message prefix";
        return s;
    }
    uint32_t hl;
    uint64_t bl;
    std::memcpy(&hl, pre, 4);
    std::memcpy(&bl, pre + 4, 8);
    if (hl == 0 || hl > kMaxHeader || bl > kMaxBlob) {
        *why = "message size out of bounds";
        return ReadStatus::Bad;
    }
    std::string h(hl, '\0');
    any = true;
    if ((s = read_exact(fd, h.data(), hl, deadline_ms, &any)) != ReadStatus::Ok) {
        *why = "truncated message header";
        return s == ReadStatus::Timeout ? s : ReadStatus::Bad;
    }
    out->blob.assign(bl, '\0');
    if (bl && (s = read_exact(fd, out->blob.data(), bl, deadline_ms, &any)) != ReadStatus::Ok) {
        *why = "truncated message blob";
        return s == ReadStatus::Timeout ? s : ReadStatus::Bad;
    }
    try {
        out->head = json::parse(h);
    } catch (const std::exception&) {
        *why = "message header is not JSON";
        return ReadStatus::Bad;
    }
    if (!out->head.is_object() || !out->head.contains("t") || !out->head["t"].is_string()) {
        *why = "message header has no type";
        return ReadStatus::Bad;
    }
    return ReadStatus::Ok;
}

}  // namespace proto
