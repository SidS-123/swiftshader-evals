// vkreplay: the trusted parent.
//
// It parses the case, starts the untrusted child (which loads the loader and
// the ICD), reads the child's messages, keeps the clock for timed runs, and is
// the only process that writes the output directory. When it runs as root (the
// grading container) the child runs as another uid that cannot write the
// output directory, the parent cannot be ptraced (not dumpable), and after the
// child ends every process of the child's uid is killed before the ledger is
// written. As an ordinary user (the model's own sandbox) the same code runs
// with the child under the same uid, where there is nothing to protect.
#include "case_model.hpp"
#include "protocol.hpp"

#include <algorithm>
#include <cerrno>
#include <climits>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <grp.h>
#include <poll.h>
#include <sstream>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

struct ParentArgs {
    std::string candidate, icd, cwd, perturb, case_path, out, assets;
    bool validate = false;
    double timeout = 600.0;
    int child_uid = 2000;
};

namespace {

[[noreturn]] void usage() {
    std::fprintf(stderr,
                 "usage: vkreplay [--candidate DIR | --icd MANIFEST] [--cwd DIR] [--perturb MODE]\n"
                 "                [--validate] [--timeout SECONDS] [--child-uid UID] CASE OUTDIR ASSETS\n"
                 "  --candidate DIR  load DIR/libvk_candidate.so as the only Vulkan driver\n"
                 "  --icd MANIFEST   load the ICD named by MANIFEST (the oracle)\n");
    std::exit(2);
}

std::string read_file(const std::string& path, size_t limit) {
    std::ifstream f(path, std::ios::binary);
    if (!f) throw CaseError("cannot read " + path);
    std::ostringstream ss;
    ss << f.rdbuf();
    std::string s = ss.str();
    if (s.size() > limit) throw CaseError(path + " is larger than the limit");
    return s;
}

bool write_file(const std::string& path, const std::string& data) {
    std::string tmp = path + ".tmp";
    FILE* f = std::fopen(tmp.c_str(), "wb");
    if (!f) return false;
    bool ok = std::fwrite(data.data(), 1, data.size(), f) == data.size();
    ok = (std::fclose(f) == 0) && ok;
    return ok && std::rename(tmp.c_str(), path.c_str()) == 0;
}

struct Output {
    std::string dir;
    uid_t uid = (uid_t)-1;
    gid_t gid = (gid_t)-1;
    std::vector<std::string> written;
    bool put(const std::string& name, const std::string& data) {
        std::string p = dir + "/" + name;
        if (!write_file(p, data)) return false;
        written.push_back(p);
        return true;
    }
    void finish() {
        if (geteuid() != 0) return;
        for (const auto& p : written) {
            if (chown(p.c_str(), uid, gid) != 0) { /* best effort */ }
            chmod(p.c_str(), 0644);
        }
    }
};

json component_list(const SnapItem& s) {
    json comps = json::array();
    if (s.aspect == VK_IMAGE_ASPECT_STENCIL_BIT) {
        comps.push_back({{"name", "S"}, {"bits", 8}, {"numeric", "UINT"}});
    } else if (s.aspect == VK_IMAGE_ASPECT_DEPTH_BIT) {
        const vkt::FormatInfo* fi = vkt::format_info(s.format);
        for (int i = 0; fi && i < fi->ncomp; ++i)
            if (!std::strcmp(fi->comps[i].name, "D"))
                comps.push_back({{"name", "D"}, {"bits", fi->comps[i].bits}, {"numeric", fi->comps[i].numeric}});
    } else {
        const vkt::FormatInfo* fi = vkt::format_info(s.format);
        for (int i = 0; fi && i < fi->ncomp; ++i)
            comps.push_back({{"name", fi->comps[i].name}, {"bits", fi->comps[i].bits}, {"numeric", fi->comps[i].numeric}});
    }
    return comps;
}

// Build a .ssnap file from the child's snapshot message. The parent decides the
// layout from the plan; the child only supplies bytes, and an item whose byte
// count disagrees with the plan is recorded as missing.
bool build_ssnap(const std::vector<SnapItem>& plan, const proto::Message& m, std::string* file, json* meta_out,
                 std::string* why) {
    const json& items = m.head.contains("items") ? m.head["items"] : json();
    if (!items.is_array() || items.size() != plan.size()) {
        *why = "snapshot message does not list the planned items";
        return false;
    }
    json meta = json::array();
    std::string payload;
    size_t pos = 0;
    for (size_t k = 0; k < plan.size(); ++k) {
        const SnapItem& s = plan[k];
        const json& it = items[k];
        bool ok = it.is_object() && it.value("ok", false) && it.value("name", std::string()) == s.name;
        json e = {{"name", s.name}};
        if (!s.allow.is_null()) e["allow"] = s.allow;
        if (s.is_buffer) {
            e["kind"] = "buffer";
            e["resource"] = s.resource;
            e["elem"] = s.elem;
            e["count"] = s.bytes / std::max(1u, elem_bytes(s.elem));
        } else {
            e["kind"] = aspect_name(s.aspect);
            e["resource"] = s.resource;
            e["format"] = vkt::enum_name("VkFormat", s.format);
            e["width"] = s.width;
            e["height"] = s.height;
            e["depth"] = s.depth;
            e["layers"] = s.layers;
            e["mip"] = s.mip;
            e["texel_bytes"] = s.texel_bytes;
            const vkt::FormatInfo* fi = vkt::format_info(s.format);
            e["packed"] = (s.aspect == VK_IMAGE_ASPECT_COLOR_BIT && fi) ? fi->packed : 0;
            e["components"] = component_list(s);
        }
        std::string reason;
        if (ok) {
            if (pos + s.bytes > m.blob.size()) {
                ok = false;
                reason = "short payload";
            }
        } else {
            reason = it.is_object() ? it.value("reason", std::string("not produced")) : "not produced";
            if (reason.size() > 300) reason.resize(300);
        }
        if (ok) {
            std::string bytes = m.blob.substr(pos, s.bytes);
            pos += s.bytes;
            // D24 depth arrives in 32-bit words whose top 8 bits are undefined.
            if (!s.is_buffer && s.aspect == VK_IMAGE_ASPECT_DEPTH_BIT &&
                (s.format == VK_FORMAT_X8_D24_UNORM_PACK32 || s.format == VK_FORMAT_D24_UNORM_S8_UINT)) {
                for (size_t b = 3; b < bytes.size(); b += 4) bytes[b] = 0;
            }
            e["offset"] = payload.size();
            e["size"] = s.bytes;
            e["missing"] = false;
            payload += bytes;
        } else {
            e["offset"] = payload.size();
            e["size"] = 0;
            e["missing"] = true;
            e["reason"] = reason;
        }
        meta.push_back(e);
    }
    json header = {{"version", 1}, {"items", meta}};
    std::string h = header.dump();
    uint32_t hl = (uint32_t)h.size();
    file->assign("SSNAP1\n");
    file->append(reinterpret_cast<const char*>(&hl), 4);
    file->append(h);
    file->append(payload);
    *meta_out = meta;
    return true;
}

void kill_uid(uid_t uid) {
    // A helper that becomes `uid` and sends SIGKILL to every process it may
    // signal: exactly the processes of that uid (never init, never itself).
    pid_t p = fork();
    if (p == 0) {
        if (setgroups(0, nullptr) != 0 || setgid(uid) != 0 || setuid(uid) != 0) _exit(1);
        kill(-1, SIGKILL);
        _exit(0);
    }
    if (p > 0) {
        int st;
        waitpid(p, &st, 0);
    }
}

std::string self_exe() {
    char buf[PATH_MAX];
    ssize_t n = readlink("/proc/self/exe", buf, sizeof(buf) - 1);
    if (n <= 0) return "vkreplay";
    buf[n] = 0;
    return buf;
}

std::string abspath(const std::string& p) {
    char buf[PATH_MAX];
    if (realpath(p.c_str(), buf)) return buf;
    return p;
}

}  // namespace

int parent_main(int argc, char** argv) {
    ParentArgs a;
    std::vector<std::string> pos;
    for (int i = 1; i < argc; ++i) {
        std::string s = argv[i];
        auto next = [&]() -> std::string {
            if (i + 1 >= argc) usage();
            return argv[++i];
        };
        if (s == "--candidate") a.candidate = next();
        else if (s == "--icd") a.icd = next();
        else if (s == "--cwd") a.cwd = next();
        else if (s == "--perturb") a.perturb = next();
        else if (s == "--validate") a.validate = true;
        else if (s == "--timeout") a.timeout = std::atof(next().c_str());
        else if (s == "--child-uid") a.child_uid = std::atoi(next().c_str());
        else if (s == "--version") { std::printf("vkreplay %s\n", VKREPLAY_VERSION); return 0; }
        else if (!s.empty() && s[0] == '-') usage();
        else pos.push_back(s);
    }
    if (pos.size() != 3 || (a.candidate.empty() == a.icd.empty())) usage();
    a.case_path = abspath(pos[0]);
    a.out = pos[1];
    a.assets = abspath(pos[2]);
    if (a.timeout <= 0) a.timeout = 600.0;
    if (const char* t = std::getenv("VKREPLAY_TIMEOUT")) {
        double v = std::atof(t);
        if (v > 0) a.timeout = std::min(a.timeout, v);
    }

    prctl(PR_SET_DUMPABLE, 0);
    const bool root = geteuid() == 0;
    if (mkdir(a.out.c_str(), 0755) == 0 && root) {
        // created here: give it to whoever owns the directory above it
        std::string up = a.out.substr(0, a.out.find_last_of('/') == std::string::npos ? 0 : a.out.find_last_of('/'));
        struct stat sp{};
        if (stat(up.empty() ? "." : up.c_str(), &sp) == 0 && chown(a.out.c_str(), sp.st_uid, sp.st_gid) != 0) { /* best effort */ }
    }
    Output out;
    out.dir = a.out;
    struct stat so{};
    if (stat(a.out.c_str(), &so) == 0) {
        out.uid = so.st_uid;
        out.gid = so.st_gid;
    }

    json ledger = {{"exit", "driver_error"}, {"vkreplay", VKREPLAY_VERSION}, {"events", json::array()},
                   {"calls", json::array()}, {"notes", json::array()}};
    ledger["mode"] = {{"target", a.candidate.empty() ? "icd" : "candidate"},
                      {"icd", a.candidate.empty() ? a.icd : a.candidate + "/libvk_candidate.so"},
                      {"perturb", a.perturb}, {"validate", a.validate}};
    auto finish = [&](const std::string& exit_status, int code) {
        ledger["exit"] = exit_status;
        out.put("ledger.json", ledger.dump(1) + "\n");
        out.finish();
        return code;
    };

    CasePlan plan;
    try {
        json doc = json::parse(read_file(a.case_path, 64u << 20));
        plan = plan_case(doc);
        ledger["case"] = plan.name;
    } catch (const std::exception& e) {
        ledger["error"] = std::string("case: ") + e.what();
        return finish("driver_error", 3);
    }

    // The child's private directory: the ICD manifest for a candidate, and its cwd.
    char tmpl[] = "/tmp/vkreplay.XXXXXX";
    std::string work = mkdtemp(tmpl) ? tmpl : "/tmp";
    chmod(work.c_str(), 0755);
    std::string icd = a.icd;
    std::string cwd = a.cwd;
    if (!a.candidate.empty()) {
        // Load a private copy: readable by the child whatever the mount's permissions,
        // and immune to changes to the original while the case runs.
        std::string src = abspath(a.candidate) + "/libvk_candidate.so";
        std::string lib = work + "/libvk_candidate.so";
        std::string bytes;
        bool copied = false;
        try {
            bytes = read_file(src, 1u << 30);
            copied = write_file(lib, bytes);
        } catch (const std::exception&) {
        }
        if (copied) {
            chmod(lib.c_str(), 0755);
        } else {
            lib = src;
            ledger["notes"].push_back({{"i", -1}, {"level", "error"}, {"msg", "candidate library not found: " + src}});
        }
        json manifest = {{"file_format_version", "1.0.1"},
                         {"ICD", {{"library_path", lib}, {"api_version", "1.3.0"}}}};
        icd = work + "/candidate_icd.json";
        write_file(icd, manifest.dump(1));
        chmod(icd.c_str(), 0644);
        if (cwd.empty()) {
            cwd = work + "/cwd";
            mkdir(cwd.c_str(), 0755);
            if (root && chown(cwd.c_str(), a.child_uid, a.child_uid) != 0) { /* best effort */ }
        }
    }
    if (cwd.empty()) cwd = work;

    int c2p[2], p2c[2];
    if (pipe(c2p) != 0 || pipe(p2c) != 0) {
        ledger["error"] = "pipe failed";
        return finish("driver_error", 3);
    }
    std::string exe = self_exe();
    const double t_start = proto::now_s();
    pid_t child = fork();
    if (child < 0) {
        ledger["error"] = "fork failed";
        return finish("driver_error", 3);
    }
    if (child == 0) {
        setsid();
        prctl(PR_SET_PDEATHSIG, SIGKILL);
        dup2(STDERR_FILENO, STDOUT_FILENO);
        close(c2p[0]);
        close(p2c[1]);
        int nul = open("/dev/null", O_RDONLY);
        if (nul >= 0) { dup2(nul, STDIN_FILENO); close(nul); }
        for (int fd = 3; fd < 1024; ++fd)
            if (fd != c2p[1] && fd != p2c[0]) close(fd);
        struct rlimit core{0, 0};
        setrlimit(RLIMIT_CORE, &core);
        if (root) {
            if (setgroups(0, nullptr) != 0 || setgid(a.child_uid) != 0 || setuid(a.child_uid) != 0) _exit(120);
        }
        if (chdir(cwd.c_str()) != 0) _exit(121);
        std::vector<std::string> env;
        for (const char* k : {"PATH", "LD_LIBRARY_PATH", "VK_LAYER_PATH"})
            if (const char* v = std::getenv(k)) env.push_back(std::string(k) + "=" + v);
        env.push_back("HOME=" + cwd);
        env.push_back("VK_DRIVER_FILES=" + icd);
        env.push_back("VK_ICD_FILENAMES=" + icd);
        env.push_back("VK_LOADER_LAYERS_DISABLE=~implicit~");
        std::vector<std::string> args = {exe, "--child", "--fd-out", std::to_string(c2p[1]), "--fd-in",
                                         std::to_string(p2c[0]), "--case", a.case_path, "--assets", a.assets};
        if (!a.perturb.empty()) { args.push_back("--perturb"); args.push_back(a.perturb); }
        if (a.validate) args.push_back("--validate");
        std::vector<char*> av, ev;
        for (auto& s : args) av.push_back(s.data());
        av.push_back(nullptr);
        for (auto& s : env) ev.push_back(s.data());
        ev.push_back(nullptr);
        execve(exe.c_str(), av.data(), ev.data());
        _exit(122);
    }
    close(c2p[1]);
    close(p2c[0]);
    signal(SIGPIPE, SIG_IGN);

    const int64_t deadline = proto::now_ms() + (int64_t)(a.timeout * 1000);
    json& events = ledger["events"];
    json& calls = ledger["calls"];
    json& notes = ledger["notes"];
    json validation = json::array();
    std::string status;               // set when the loop ends abnormally
    bool done = false;
    int64_t last_i = -1;
    std::vector<bool> emitted(plan.ops.size(), false);
    int64_t run_i = -1;
    double run_t0 = 0;
    uint64_t dropped_calls = 0;
    // The direct child is reaped as soon as it exits: a process it left behind
    // can hold the pipe open, and must not turn a crash into a timeout.
    bool reaped = false;
    int reaped_status = 0;

    while (true) {
        pollfd pfd{c2p[0], POLLIN, 0};
        int pr = poll(&pfd, 1, 200);
        if (pr < 0 && errno != EINTR) { status = "protocol_error"; ledger["error"] = "poll failed"; break; }
        if (pr <= 0) {
            if (proto::now_ms() >= deadline) { status = "timeout"; break; }
            if (!reaped && waitpid(child, &reaped_status, WNOHANG) == child) reaped = true;
            if (reaped) break;   // gone, and nothing left to read
            continue;
        }
        proto::Message m;
        std::string why;
        proto::ReadStatus rs = proto::read_msg(c2p[0], &m, deadline, &why);
        if (rs == proto::ReadStatus::Eof) break;
        if (rs == proto::ReadStatus::Timeout) { status = "timeout"; break; }
        if (rs == proto::ReadStatus::Bad) { status = "protocol_error"; ledger["error"] = why; break; }
        const std::string t = m.head["t"].get<std::string>();
        int64_t i = m.head.contains("i") && m.head["i"].is_number_integer() ? m.head["i"].get<int64_t>() : -1;
        auto violation = [&](const std::string& what) {
            status = "protocol_error";
            ledger["error"] = what + " (op " + std::to_string(i) + ")";
        };
        if (t == "call") {
            if (calls.size() < 200000) {
                std::string f = m.head.value("f", std::string("?")), r = m.head.value("r", std::string("?"));
                f.resize(std::min<size_t>(f.size(), 80));
                r.resize(std::min<size_t>(r.size(), 80));
                calls.push_back(json::array({i, f, r}));
            } else {
                dropped_calls++;
            }
            continue;
        }
        if (t == "note" || t == "validation") {
            json& dst = t == "note" ? notes : validation;
            if (dst.size() < 1000) {
                std::string msg = m.head.value("msg", std::string());
                if (msg.size() > 2000) msg.resize(2000);
                dst.push_back({{"i", i}, {"level", m.head.value("level", std::string("info"))}, {"msg", msg}});
            }
            continue;
        }
        if (t == "done") { done = true; break; }
        if (t == "case_error") {
            status = "driver_error";
            std::string msg = m.head.value("msg", std::string());
            if (msg.size() > 2000) msg.resize(2000);
            ledger["error"] = "case: " + msg;
            break;
        }
        if (i < 0 || i >= (int64_t)plan.ops.size() || i < last_i) { violation("message for an op out of order"); break; }
        const OpPlan& op = plan.ops[i];
        if (t == "event") {
            if (op.out != OutKind::Event || emitted[i] || !m.head.contains("value")) { violation("unexpected event"); break; }
            emitted[i] = true;
            last_i = i;
            events.push_back({{"op", "query"}, {"kind", op.op}, {"name", op.name}, {"value", m.head["value"]}});
            continue;
        }
        if (t == "run_ready") {
            if (op.out != OutKind::Run || emitted[i] || run_i >= 0) { violation("unexpected run_ready"); break; }
            run_i = i;
            last_i = i;
            run_t0 = proto::now_s();
            json go = {{"t", "go"}, {"i", i}};
            if (!proto::write_msg(p2c[1], go)) { status = "crash"; break; }
            continue;
        }
        if (t == "snapshot") {
            bool is_run = op.out == OutKind::Run;
            if ((op.out != OutKind::Snapshot && !is_run) || emitted[i] || (is_run && run_i != i)) {
                violation("unexpected snapshot");
                break;
            }
            double t1 = proto::now_s();
            emitted[i] = true;
            last_i = i;
            std::string file;
            json meta;
            if (!build_ssnap(op.items, m, &file, &meta, &why)) { violation(why); break; }
            const std::string snap_name = is_run ? op.snap_name : op.name;
            if (is_run) {
                events.push_back({{"op", "run"}, {"name", op.name}, {"wall_seconds", t1 - run_t0}});
                run_i = -1;
            }
            const std::string fname = snap_name + ".ssnap";
            out.put(fname, file);
            events.push_back({{"op", "snapshot"}, {"name", snap_name}, {"files", {{"snap", fname}}}, {"items", meta}});
            continue;
        }
        violation("unknown message type '" + t + "'");
        break;
    }

    // Stop the child and everything it started, then collect its status. After
    // "done" the child gets 10 s to exit on its own (a candidate library can
    // hang in its destructors); that is recorded as a crash, not ok.
    if (!done || !status.empty()) kill(-child, SIGKILL);
    int st = reaped_status;
    pid_t w = reaped ? child : 0;
    const int64_t exit_deadline = proto::now_ms() + 10000;
    while (!reaped && (w = waitpid(child, &st, WNOHANG)) == 0 && proto::now_ms() < exit_deadline) usleep(20000);
    if (w == 0) {
        kill(-child, SIGKILL);
        w = waitpid(child, &st, 0);
        if (status.empty()) {
            status = "crash";
            ledger["error"] = "the child did not exit within 10 s of finishing the case";
        }
    }
    if (root) kill_uid(a.child_uid);
    else kill(-child, SIGKILL);
    close(c2p[0]);
    close(p2c[1]);

    json cinfo = {{"seconds", proto::now_s() - t_start}};
    if (w == child && WIFEXITED(st)) cinfo["exit_code"] = WEXITSTATUS(st);
    if (w == child && WIFSIGNALED(st)) cinfo["signal"] = WTERMSIG(st);
    ledger["child"] = cinfo;
    if (dropped_calls) ledger["calls_dropped"] = dropped_calls;
    if (a.validate) ledger["validation"] = validation;

    std::string exit_status;
    if (!status.empty()) exit_status = status;
    else if (done && w == child && WIFEXITED(st) && WEXITSTATUS(st) == 0) exit_status = "ok";
    else if (w == child && WIFEXITED(st) && WEXITSTATUS(st) == 120) exit_status = "driver_error", ledger["error"] = "could not drop privileges";
    else exit_status = "crash";
    int code = exit_status == "ok" ? 0 : exit_status == "driver_error" ? 3 : 1;
    return finish(exit_status, code);
}
