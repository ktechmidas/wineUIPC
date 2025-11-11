#define _CRT_SECURE_NO_WARNINGS 1
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif

#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#pragma comment(lib, "Ws2_32.lib")

#define IPC_MAP_BYTES  (0x7F00u + 0x100u)
#define FS6IPC_READSTATEDATA_ID  1
#define FS6IPC_WRITESTATEDATA_ID 2

typedef struct {
    uint32_t dwId;
    uint32_t dwOffset;
    uint32_t nBytes;
    uint32_t pDest;
} FS6IPC_READSTATEDATA_HDR;

typedef struct {
    uint32_t dwId;
    uint32_t dwOffset;
    uint32_t nBytes;
} FS6IPC_WRITESTATEDATA_HDR;

typedef struct {
    ATOM     atom;
    HANDLE   hMap;
    uint8_t* view;
    size_t   length;
} SharedCtx;

static SharedCtx g_shared = {0};
static UINT g_uMsgFSASM = 0;
static UINT g_uMsgFS6IPC = 0;
static SOCKET g_sock = INVALID_SOCKET;
static char g_host[128] = "127.0.0.1";
static uint16_t g_port = 9000;
static FILE* g_log = NULL;

static void log_printf(const char* fmt, ...){
    if (!g_log){
        g_log = fopen("uipc_bridge.log", "a");
        if (g_log){
            fprintf(g_log, "--- uipc_bridge start pid=%lu ---\n", (unsigned long)GetCurrentProcessId());
            fflush(g_log);
        }
    }
    if (!g_log) return;
    va_list ap;
    va_start(ap, fmt);
    vfprintf(g_log, fmt, ap);
    fputc('\n', g_log);
    fflush(g_log);
    va_end(ap);
}

static void log_close(void){
    if (g_log){
        fprintf(g_log, "--- uipc_bridge stop ---\n");
        fclose(g_log);
        g_log = NULL;
    }
}

static void close_shared_ctx(void){
    if (g_shared.view){
        UnmapViewOfFile(g_shared.view);
        g_shared.view = NULL;
    }
    if (g_shared.hMap){
        CloseHandle(g_shared.hMap);
        g_shared.hMap = NULL;
    }
    g_shared.atom = 0;
    g_shared.length = 0;
}

static BOOL ensure_shared_ctx(ATOM atom){
    if (!atom) return FALSE;
    if (g_shared.atom == atom && g_shared.view) return TRUE;

    close_shared_ctx();

    wchar_t name[256];
    UINT len = GlobalGetAtomNameW(atom, name, (UINT)(sizeof(name)/sizeof(name[0])));
    if (!len){
        log_printf("GlobalGetAtomNameW failed err=%lu", GetLastError());
        return FALSE;
    }

    HANDLE hMap = OpenFileMappingW(FILE_MAP_READ | FILE_MAP_WRITE, FALSE, name);
    if (!hMap){
        log_printf("OpenFileMappingW failed err=%lu", GetLastError());
        return FALSE;
    }

    uint8_t* view = (uint8_t*)MapViewOfFile(hMap, FILE_MAP_ALL_ACCESS, 0, 0, 0);
    if (!view){
        log_printf("MapViewOfFile failed err=%lu", GetLastError());
        CloseHandle(hMap);
        return FALSE;
    }

    g_shared.atom = atom;
    g_shared.hMap = hMap;
    g_shared.view = view;
    g_shared.length = IPC_MAP_BYTES;
    return TRUE;
}

static size_t calc_block_len(const uint8_t* base, size_t avail){
    size_t pos = 0;
    while (pos + sizeof(uint32_t) <= avail){
        uint32_t id = *(const uint32_t*)(base + pos);
        if (id == 0){
            pos += sizeof(uint32_t);
            return pos;
        }
        if (id == FS6IPC_READSTATEDATA_ID){
            if (pos + sizeof(FS6IPC_READSTATEDATA_HDR) > avail) return 0;
            const FS6IPC_READSTATEDATA_HDR* hdr = (const FS6IPC_READSTATEDATA_HDR*)(base + pos);
            pos += sizeof(*hdr);
            if (pos + hdr->nBytes > avail) return 0;
            pos += hdr->nBytes;
        } else if (id == FS6IPC_WRITESTATEDATA_ID){
            if (pos + sizeof(FS6IPC_WRITESTATEDATA_HDR) > avail) return 0;
            const FS6IPC_WRITESTATEDATA_HDR* hdr = (const FS6IPC_WRITESTATEDATA_HDR*)(base + pos);
            pos += sizeof(*hdr);
            if (pos + hdr->nBytes > avail) return 0;
            pos += hdr->nBytes;
        } else {
            return 0;
        }
    }
    return 0;
}

static BOOL hex_encode(const uint8_t* data, size_t len, char** out_str, size_t* out_len){
    size_t need = len * 2;
    char* buf = (char*)malloc(need + 1);
    if (!buf) return FALSE;
    for (size_t i = 0; i < len; ++i){
        sprintf(buf + (i * 2), "%02X", data[i]);
    }
    buf[need] = '\0';
    *out_str = buf;
    if (out_len) *out_len = need;
    return TRUE;
}

static BOOL hex_decode(const char* hex, uint8_t* out, size_t out_cap, size_t* out_len){
    size_t len = strlen(hex);
    if (len % 2 != 0) return FALSE;
    size_t bytes = len / 2;
    if (bytes > out_cap) return FALSE;
    for (size_t i = 0; i < bytes; ++i){
        unsigned int val;
        if (sscanf(hex + i * 2, "%02X", &val) != 1) return FALSE;
        out[i] = (uint8_t)val;
    }
    *out_len = bytes;
    return TRUE;
}

static void close_socket(void){
    if (g_sock != INVALID_SOCKET){
        closesocket(g_sock);
        g_sock = INVALID_SOCKET;
    }
}

static BOOL ensure_socket(void){
    if (g_sock != INVALID_SOCKET) return TRUE;

    SOCKET s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s == INVALID_SOCKET){
        log_printf("socket() failed err=%ld", WSAGetLastError());
        return FALSE;
    }
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons(g_port);
    if (inet_pton(AF_INET, g_host, &addr.sin_addr) != 1){
        log_printf("inet_pton failed for host %s", g_host);
        closesocket(s);
        return FALSE;
    }
    if (connect(s, (struct sockaddr*)&addr, sizeof(addr)) == SOCKET_ERROR){
        log_printf("connect failed err=%ld", WSAGetLastError());
        closesocket(s);
        return FALSE;
    }
    g_sock = s;
    return TRUE;
}

static BOOL recv_line(char** line_out){
    char* buf = NULL;
    size_t len = 0;
    size_t cap = 0;
    char chunk[1024];
    while (1){
        int got = recv(g_sock, chunk, sizeof(chunk), 0);
        if (got <= 0){
            free(buf);
            log_printf("recv failed err=%ld", WSAGetLastError());
            close_socket();
            return FALSE;
        }
        if (len + (size_t)got + 1 > cap){
            size_t new_cap = (len + got + 1) * 2;
            char* tmp = (char*)realloc(buf, new_cap);
            if (!tmp){
                free(buf);
                return FALSE;
            }
            buf = tmp;
            cap = new_cap;
        }
        memcpy(buf + len, chunk, (size_t)got);
        len += (size_t)got;
        if (memchr(chunk, '\n', got)){
            break;
        }
    }
    buf[len] = '\0';
    char* nl = strchr(buf, '\n');
    if (nl) *nl = '\0';
    *line_out = buf;
    return TRUE;
}

static BOOL send_json_request(const uint8_t* data, size_t len, DWORD dwData, DWORD cbData, uint8_t* outBuf, size_t outCap, size_t* outLen){
    if (!ensure_socket()) return FALSE;

    char* hex = NULL;
    size_t hex_len = 0;
    if (!hex_encode(data, len, &hex, &hex_len)){
        return FALSE;
    }

    size_t json_cap = hex_len + 128;
    char* json = (char*)malloc(json_cap);
    if (!json){
        free(hex);
        return FALSE;
    }

    int written = _snprintf(json, json_cap, "{\"cmd\":\"ipc\",\"dwData\":%lu,\"cbData\":%lu,\"hex\":\"%s\"}\n",
                            (unsigned long)dwData, (unsigned long)cbData, hex);
    free(hex);
    if (written < 0 || (size_t)written >= json_cap){
        free(json);
        return FALSE;
    }

    size_t to_send = (size_t)written;
    size_t sent = 0;
    while (sent < to_send){
        int res = send(g_sock, json + sent, (int)(to_send - sent), 0);
        if (res <= 0){
            free(json);
            log_printf("send failed err=%ld", WSAGetLastError());
            close_socket();
            return FALSE;
        }
        sent += (size_t)res;
    }
    free(json);

    char* line = NULL;
    if (!recv_line(&line)){
        return FALSE;
    }

    BOOL ok = strstr(line, "\"ok\":true") != NULL;
    if (!ok){
        log_printf("bridge reply error: %s", line);
        free(line);
        return FALSE;
    }

    char* hex_field = strstr(line, "\"replyHex\":\"");
    if (!hex_field){
        free(line);
        return FALSE;
    }
    hex_field += strlen("\"replyHex\":\"");
    char* end = strchr(hex_field, '"');
    if (!end){
        free(line);
        return FALSE;
    }
    size_t hex_field_len = (size_t)(end - hex_field);
    char* hex_reply = (char*)malloc(hex_field_len + 1);
    if (!hex_reply){
        free(line);
        return FALSE;
    }
    memcpy(hex_reply, hex_field, hex_field_len);
    hex_reply[hex_field_len] = '\0';

    size_t reply_bytes = 0;
    BOOL decode_ok = hex_decode(hex_reply, outBuf, outCap, &reply_bytes);
    free(hex_reply);
    free(line);
    if (!decode_ok){
        return FALSE;
    }
    *outLen = reply_bytes;
    return TRUE;
}

static BOOL forward_block(uint32_t dwData, uint8_t* block, size_t len){
    size_t reply_len = 0;
    if (!send_json_request(block, len, dwData, (DWORD)len, block, len, &reply_len)){
        return FALSE;
    }
    if (reply_len != len){
        log_printf("reply length mismatch req=%zu reply=%zu", len, reply_len);
        return FALSE;
    }
    return TRUE;
}

static BOOL forward_shared_request(ATOM atom, size_t offset){
    if (!ensure_shared_ctx(atom)){
        return FALSE;
    }
    if (offset >= g_shared.length){
        return FALSE;
    }
    uint8_t* base = g_shared.view + offset;
    size_t avail = g_shared.length - offset;
    size_t block_len = calc_block_len(base, avail);
    if (!block_len){
        block_len = avail;
    }
    return forward_block(0, base, block_len);
}

static LRESULT handle_registered_request(WPARAM wParam, LPARAM lParam){
    if (lParam < 0){
        return 0;
    }
    if (wParam == 0){
        return 1;
    }
    if (forward_shared_request((ATOM)wParam, (size_t)lParam)){
        return 1;
    }
    return 0;
}

static LRESULT CALLBACK WndProc(HWND hWnd, UINT msg, WPARAM wParam, LPARAM lParam){
    if (msg == g_uMsgFSASM){
        return handle_registered_request(wParam, lParam);
    }
    if (msg == g_uMsgFS6IPC){
        return 1;
    }
    if (msg == WM_COPYDATA){
        COPYDATASTRUCT* cds = (COPYDATASTRUCT*)lParam;
        if (!cds || !cds->lpData || cds->cbData == 0){
            return TRUE;
        }
        if (forward_block((uint32_t)cds->dwData, (uint8_t*)cds->lpData, cds->cbData)){
            return TRUE;
        }
        return FALSE;
    }
    if (msg == WM_DESTROY){
        close_shared_ctx();
        close_socket();
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProc(hWnd, msg, wParam, lParam);
}

static void parse_env(void){
    char buf[64];
    DWORD got = GetEnvironmentVariableA("XPC_HOST", buf, (DWORD)sizeof(buf));
    if (got > 0 && got < sizeof(buf)){
        strncpy(g_host, buf, sizeof(g_host));
        g_host[sizeof(g_host)-1] = '\0';
    }
    got = GetEnvironmentVariableA("XPC_PORT", buf, (DWORD)sizeof(buf));
    if (got > 0 && got < sizeof(buf)){
        int port = atoi(buf);
        if (port > 0 && port < 65536){
            g_port = (uint16_t)port;
        }
    }
}

int WINAPI WinMain(HINSTANCE hInstance, HINSTANCE hPrevInstance, LPSTR lpCmdLine, int nCmdShow){
    (void)hPrevInstance; (void)lpCmdLine; (void)nCmdShow;
    atexit(log_close);
    parse_env();

    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2,2), &wsa) != 0){
        MessageBoxW(NULL, L"WSAStartup failed", L"uipc_bridge", MB_ICONERROR);
        return 1;
    }

    g_uMsgFS6IPC = RegisterWindowMessageW(L"FS6IPC");
    g_uMsgFSASM  = RegisterWindowMessageW(L"FSASMLIB:IPC");

    WNDCLASSW wc = {0};
    wc.lpfnWndProc = WndProc;
    wc.hInstance = hInstance;
    wc.lpszClassName = L"UIPCMAIN";
    if (!RegisterClassW(&wc)){
        MessageBoxW(NULL, L"RegisterClass failed", L"uipc_bridge", MB_ICONERROR);
        WSACleanup();
        return 1;
    }

    HWND hwnd = CreateWindowExW(
        0, wc.lpszClassName, L"UIPCMAIN",
        WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT, CW_USEDEFAULT, 300, 200,
        NULL, NULL, hInstance, NULL
    );
    if (!hwnd){
        MessageBoxW(NULL, L"CreateWindowEx failed", L"uipc_bridge", MB_ICONERROR);
        WSACleanup();
        return 1;
    }

    ShowWindow(hwnd, SW_SHOWNOACTIVATE);
    UpdateWindow(hwnd);

    MSG msg;
    while (GetMessageW(&msg, NULL, 0, 0) > 0){
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }

    close_socket();
    WSACleanup();
    return 0;
}
