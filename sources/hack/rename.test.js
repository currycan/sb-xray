/**
 * rename.js 自测（零依赖，node 直跑）：
 *   node sources/hack/rename.test.js
 *
 * 覆盖：
 *   - 名称识别失败的纯 IP 节点经 geoFallback 正确补旗（rackenerd 场景）
 *   - 公网域名 server 走单条接口补旗
 *   - 地理查询失败 / 无联网能力时优雅保持 🏳️（不抛）
 *   - 命名节点零回归，且不触发任何地理请求
 *   - 缓存命中后第二轮零网络请求
 *
 * 通过 mock 全局 $.http 与 scriptResourceCache 模拟 Sub-Store(Node) 运行时。
 */

'use strict';

const fs = require('fs');
const path = require('path');
const assert = require('assert');

const SRC = fs.readFileSync(path.join(__dirname, 'rename.js'), 'utf8');

// 在独立函数作用域里求值 rename.js 并取出 operator；$ / scriptResourceCache
// 以全局自由变量解析，便于逐用例 mock。
const operator = new Function(SRC + '\n;return operator;')();

// ── mock 基础设施 ─────────────────────────────────────────────────────────────

function makeCache() {
    const store = new Map();
    return {
        get: (k) => store.get(k),
        set: (k, v) => { store.set(k, v); },
        _store: store,
    };
}

const calls = { post: 0, get: 0 };

function installHttp({ batch, single, throwOn } = {}) {
    calls.post = 0;
    calls.get = 0;
    globalThis.$ = {
        http: {
            post: async ({ body }) => {
                calls.post += 1;
                if (throwOn === 'post') throw new Error('network down');
                const ips = JSON.parse(body);
                const arr = ips.map((q) => (batch && batch[q])
                    ? { status: 'success', countryCode: batch[q], query: q }
                    : { status: 'fail', query: q });
                return { body: JSON.stringify(arr), statusCode: 200 };
            },
            get: async ({ url }) => {
                calls.get += 1;
                if (throwOn === 'get') throw new Error('network down');
                // 从 url 还原 host：.../json/<host>?fields=...
                const host = decodeURIComponent(url.split('/json/')[1].split('?')[0]);
                const cc = single && single[host];
                return {
                    body: JSON.stringify(cc
                        ? { status: 'success', countryCode: cc, query: host }
                        : { status: 'fail', query: host }),
                    statusCode: 200,
                };
            },
        },
    };
}

function clearRuntime() {
    delete globalThis.$;
    delete globalThis.scriptResourceCache;
}

// ── 测试运行器 ────────────────────────────────────────────────────────────────

let passed = 0;
const failures = [];
async function test(name, fn) {
    try {
        await fn();
        passed += 1;
        console.log(`  ✅ ${name}`);
    } catch (err) {
        failures.push({ name, err });
        console.log(`  ❌ ${name}\n     ${err.message}`);
    }
}

// ── 用例 ──────────────────────────────────────────────────────────────────────

(async () => {
    await test('纯 IP 节点：名称识别失败 → batch 补旗为 🇺🇸 美国', async () => {
        globalThis.scriptResourceCache = makeCache();
        installHttp({ batch: { '203.0.113.10': 'US' } });
        const out = await operator([{ name: 'rackenerd2.ansandy.com', type: 'vless', server: '203.0.113.10' }]);
        assert.strictEqual(out[0].name, '🇺🇸 vless ✈ 美国 ✈ rackenerd2.ansandy.com');
        assert.strictEqual(calls.post, 1, 'IP 应走 batch 一次');
        assert.strictEqual(calls.get, 0, 'IP 不应走单条接口');
    });

    await test('公网域名 server → 单条接口补旗为 🇯🇵 日本', async () => {
        globalThis.scriptResourceCache = makeCache();
        installHttp({ single: { 'box.example.net': 'JP' } });
        const out = await operator([{ name: 'box.example.net', type: 'vless', server: 'box.example.net' }]);
        assert.ok(out[0].name.includes('🇯🇵'), `应含 🇯🇵，实际：${out[0].name}`);
        assert.ok(out[0].name.includes('日本'), `应含 日本，实际：${out[0].name}`);
        assert.strictEqual(calls.get, 1, '域名应走单条接口一次');
        assert.strictEqual(calls.post, 0, '域名不应走 batch');
    });

    await test('server 带端口（域名:port）→ 去端口走单条接口补旗（截图场景）', async () => {
        globalThis.scriptResourceCache = makeCache();
        installHttp({ single: { 'racknerd2.ansandy.com': 'US' } });
        const out = await operator([{ name: 'hysteria2 racknerd good hosting', type: 'hysteria2', server: 'racknerd2.ansandy.com:6443' }]);
        assert.ok(out[0].name.includes('🇺🇸') && out[0].name.includes('美国'), `应补成美国，实际：${out[0].name}`);
        assert.strictEqual(calls.get, 1, '带端口域名应去端口走单条接口');
        assert.strictEqual(calls.post, 0, '不应误判为 IPv6 走 batch');
    });

    await test('server 带端口（IP:port）→ 去端口走 batch 补旗', async () => {
        globalThis.scriptResourceCache = makeCache();
        installHttp({ batch: { '203.0.113.10': 'US' } });
        const out = await operator([{ name: 'somebox', type: 'vless', server: '203.0.113.10:443' }]);
        assert.ok(out[0].name.includes('🇺🇸'), `应补成美国，实际：${out[0].name}`);
        assert.strictEqual(calls.post, 1, 'IP:port 去端口后走 batch');
        assert.strictEqual(calls.get, 0);
    });

    await test('地理查询抛错 → 保持 🏳️（优雅降级，不抛）', async () => {
        globalThis.scriptResourceCache = makeCache();
        installHttp({ throwOn: 'post' });
        const out = await operator([{ name: 'rackenerd2.ansandy.com', type: 'vless', server: '203.0.113.10' }]);
        assert.strictEqual(out[0].name, '🏳️ vless ✈ rackenerd2.ansandy.com');
    });

    await test('查询结果 fail（私有/不可解析）→ 保持 🏳️', async () => {
        globalThis.scriptResourceCache = makeCache();
        installHttp({ batch: {} }); // 全部 fail
        const out = await operator([{ name: 'rackenerd2.ansandy.com', type: 'vless', server: '203.0.113.10' }]);
        assert.strictEqual(out[0].name, '🏳️ vless ✈ rackenerd2.ansandy.com');
    });

    await test('无联网能力（$ 缺失）→ 保持 🏳️，不抛', async () => {
        clearRuntime();
        const out = await operator([{ name: 'rackenerd2.ansandy.com', type: 'vless', server: '203.0.113.10' }]);
        assert.strictEqual(out[0].name, '🏳️ vless ✈ rackenerd2.ansandy.com');
    });

    await test('命名节点零回归，且不触发任何地理请求', async () => {
        globalThis.scriptResourceCache = makeCache();
        installHttp({ batch: { '203.0.113.10': 'US' } });
        const out = await operator([
            { name: '🇭🇰 Hong Kong丨01', type: 'ss', server: '203.0.113.10' },
            { name: 'Taiwan-Hsinchu-02-1.0倍', type: 'ss', server: '203.0.113.10' },
            { name: '🇺🇸美国洛杉矶v6 08 🎯 udp', type: 'ss', server: '203.0.113.10' },
        ]);
        assert.strictEqual(out.find(p => p.name.includes('香港')).name, '🇭🇰 ss ✈ 香港');
        assert.ok(out.some(p => p.name.includes('🇹🇼') && p.name.includes('台湾')), '台湾节点应正确标旗');
        assert.ok(out.some(p => p.name.includes('🇺🇸') && p.name.includes('美国')), '美国节点应正确标旗');
        assert.ok(out.every(p => !p.name.includes('🏳️')), '不应有白旗');
        assert.strictEqual(calls.post, 0, '已识别节点不应触发 batch');
        assert.strictEqual(calls.get, 0, '已识别节点不应触发单条接口');
    });

    await test('缓存命中：第二轮零网络请求', async () => {
        globalThis.scriptResourceCache = makeCache();
        installHttp({ batch: { '203.0.113.10': 'US' } });
        const node = () => [{ name: 'rackenerd2.ansandy.com', type: 'vless', server: '203.0.113.10' }];
        const first = await operator(node());
        assert.strictEqual(first[0].name, '🇺🇸 vless ✈ 美国 ✈ rackenerd2.ansandy.com');
        const postAfterFirst = calls.post;
        const second = await operator(node());
        assert.strictEqual(second[0].name, '🇺🇸 vless ✈ 美国 ✈ rackenerd2.ansandy.com');
        assert.strictEqual(calls.post, postAfterFirst, '第二轮应命中缓存，不再请求');
    });

    console.log(`\n${passed} passed, ${failures.length} failed`);
    if (failures.length > 0) process.exit(1);
})();
