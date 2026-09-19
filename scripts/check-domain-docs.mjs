#!/usr/bin/env node
/**
 * 领域模型文档（CONTEXT.md、docs/adr/）的结构门禁。
 *
 * 规则的定义处是上游 skill：`.codex/skills/domain-modeling/ADR-FORMAT.md` 讲 ADR 写成什么样，
 * `CONTEXT-FORMAT.md` 讲术语表写成什么样。本文件是那两份散文的机器版——把「写成什么样算合规」
 * 翻成能判断的检查，规则常量集中在下面的 RULES，每条注明出自上游哪一句。skill 目录是上游原文，
 * 一律不改；本仓库比上游多要求的只有一条（frontmatter 的 status 必填），同样写在 RULES 旁边。
 * 规则要变，先看上游怎么说，再改这里；不要在别处再放一份规则文件，第二份必然漂移。
 *
 * 为什么是结构校验而不是「检查有没有调用过 skill」：调用记录拦不住真正的失败模式——模型完全
 * 可以不调 skill 而写出一份格式对、判据错的 ADR。写出来的东西合不合规是确定的，可以机械判断；
 * 判据（值不值得写）机器判不了，那部分靠根 AGENTS.md 的写入时机约束。
 *
 * 用法：
 *   node scripts/check-domain-docs.mjs                  # 全量检查，报告打到 stdout；有违规退出 1
 *   node scripts/check-domain-docs.mjs --stdin          # hook 模式：从 stdin 读一次工具调用
 *   node scripts/check-domain-docs.mjs --files a.md b.md # 只检查指定文件
 *   node scripts/check-domain-docs.mjs --files a.md --root /tmp/x  # 校验仓库外的一份拷贝（git hook 用）
 *
 * hook 模式对**已存在的文件**按「不允许变得更差」判定：只拦本次新增的违规，不拦修复过程中的
 * 中间状态。新文件没有旧版，全部违规都算新增。
 *
 * hook 模式的输出契约只有一条：**违规时退出 2、把理由写到 stderr**。Claude 侧在本仓库实测过
 * （PreToolUse hook 收到退出码 2 后拒绝写入，并把 stderr 回给模型）；Codex 与 Gemini 按同一约定
 * 接入，未在本仓库实测；pi 的扩展自己读 stderr。不为每个 harness 分别输出结构化 JSON：那会让
 * 某些 harness 拦住了却不告诉模型原因，而模型看不到理由就只会盲试。
 *
 * hook 模式读的 JSON（字段名按 harness 归一化，两种拼法都认；Claude/Codex/Gemini 的参数
 * 嵌在 tool_input 里，pi 的在顶层，脚本先拆一层）：
 *   { "path"|"file_path": "docs/adr/00xx-x.md",
 *     "content"|"file_text": "…",                       // 整体写入
 *     "edits": [{"oldText"|"old_string", "newText"|"new_string"}],  // 增量编辑
 *     "old_string", "new_string" }                      // 单段编辑
 *
 * 退出码：0 通过（可能有警告）；1 有违规（全量模式）；2 有违规（hook 模式）；3 脚本自身出错。
 * **认不出的输入形状按通过处理**（不阻塞开发），真正的兜底是 git hook 对落盘文件的检查。
 */

import { readFileSync, readdirSync, existsSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const SKILL_DIR = '.codex/skills/domain-modeling';

// ------------------------------------------------------------------- 规则

/**
 * 上游两份格式说明的机器版。改动前先读那两份原文：这里只能比它们严，不能和它们打架。
 */
const RULES = {
  adr: {
    dir: 'docs/adr',
    // ADR-FORMAT.md：「ADRs live in docs/adr/ and use sequential numbering: 0001-slug.md」。
    // slug 用 ASCII kebab-case 是根 AGENTS.md「作用域与协作」的要求。
    filenamePattern: /^[0-9]{4}-[a-z0-9]+(-[a-z0-9]+)*\.md$/,
    // ADR-FORMAT.md 只定义了 Status 这一个 frontmatter 键，其余键都是自造格式。
    // 上游把它列为可选（「useful when decisions are revisited」）；本仓库要求必填（根 AGENTS.md
    // 「仓库约定」）：没有它，读者无法机械判断一份 ADR 今天还有效没有。
    frontmatterKeys: ['status'],
    // ADR-FORMAT.md：`proposed | accepted | deprecated | superseded by ADR-NNNN`。
    statusValues: ['proposed', 'accepted', 'deprecated'],
    supersededPattern: /^superseded by ADR-([0-9]{4})$/,
    // ADR-FORMAT.md 的模板只有一个 `# {Short title of the decision}`；编号已在文件名里，标题不重复写。
    forbiddenTitlePrefix: /^ADR\s*[0-9]{4}\s*[:：]/,
    // ADR-FORMAT.md「Optional sections」只有这两个，且「Most ADRs won't need them」。
    allowedSections: ['Considered Options', 'Consequences'],
    // ADR-FORMAT.md「An ADR can be a single paragraph」。超过只提醒不拦：硬上限会逼人删掉理由。
    warnLines: 60,
  },
  context: {
    file: 'CONTEXT.md',
    // CONTEXT-FORMAT.md：词条形如 `**Order**:`，独占一行，下一行起是定义。
    entryPattern: /^\*\*(.+?)\*\*[：:]\s*$/,
    // CONTEXT-FORMAT.md「Keep definitions tight. One or two sentences max.」超过只提醒。
    warnDefinitionLines: 5,
  },
};

// ------------------------------------------------------------ 极简 YAML 子集

/**
 * 只解析 frontmatter 用得到的 YAML 子集：`key: value`、`key: [a, b]`、以及
 * `key:` 后跟 `- item` 行组成的列表。合规的 ADR 只有 status 一个键，列表形态只是为了把
 * 自造的键（比如某个数组）也读出来好报错，不值得为它拉一个 yaml 依赖。
 */
function parseFrontmatter(text) {
  const match = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(text);
  if (!match) return null;
  const data = {};
  let currentKey = null;
  for (const rawLine of match[1].split(/\r?\n/)) {
    const line = rawLine.trimEnd();
    if (line.trim() === '' || line.trimStart().startsWith('#')) continue;
    const listItem = /^\s*-\s*(.+)$/.exec(line);
    if (listItem && currentKey) {
      if (!Array.isArray(data[currentKey])) data[currentKey] = [];
      data[currentKey].push(unquote(listItem[1]));
      continue;
    }
    const pair = /^([A-Za-z0-9_-]+):\s*(.*)$/.exec(line);
    if (!pair) continue;
    currentKey = pair[1];
    const value = pair[2].trim();
    if (value === '') {
      data[currentKey] = [];
    } else if (value.startsWith('[') && value.endsWith(']')) {
      data[currentKey] = value
        .slice(1, -1)
        .split(',')
        .map((item) => unquote(item.trim()))
        .filter((item) => item !== '');
    } else {
      data[currentKey] = unquote(value);
    }
  }
  return { data, body: text.slice(match[0].length) };
}

function unquote(value) {
  const trimmed = value.trim();
  if (
    (trimmed.startsWith('"') && trimmed.endsWith('"')) ||
    (trimmed.startsWith("'") && trimmed.endsWith("'"))
  ) {
    return trimmed.slice(1, -1);
  }
  return trimmed;
}

// ------------------------------------------------------------------- 校验

/** 收集目录里已存在的 ADR 编号，用于校验取代指向不悬空。 */
function existingAdrNumbers() {
  const dir = join(REPO_ROOT, RULES.adr.dir);
  if (!existsSync(dir)) return new Set();
  const numbers = new Set();
  for (const name of readdirSync(dir)) {
    const match = /^([0-9]{4})-/.exec(name);
    if (match && name.endsWith('.md')) numbers.add(match[1]);
  }
  return numbers;
}

/**
 * 找出解析不到文件的相对链接。
 *
 * 只看 Markdown 链接语法 `[text](target)`，先剥掉代码块与行内代码（里面的东西是示例，不是链接）；
 * 带协议的（http、mailto、javascript）与纯锚点跳过，`#片段` 去掉后再查。链接按本文件所在目录、
 * 相对仓库根解析——pre-commit 把暂存内容拷到临时目录校验时，被指向的文件仍在仓库里，所以这里
 * 不看 --root。拦的是「删过或改名过文件，指向它的链接没同步」。
 */
function brokenLinks(text, baseDir) {
  const stripped = text.replace(/```[\s\S]*?```/g, '').replace(/`[^`\n]*`/g, '');
  const broken = [];
  for (const match of stripped.matchAll(/\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g)) {
    const raw = match[1];
    if (/^[a-z][a-z0-9+.-]*:/i.test(raw) || raw.startsWith('#')) continue;
    let target = raw.split('#')[0];
    if (target === '') continue;
    try {
      target = decodeURIComponent(target);
    } catch {
      // 不是合法的百分号编码就按原样找
    }
    if (!existsSync(resolve(REPO_ROOT, baseDir, target))) broken.push(raw);
  }
  return broken;
}

function validateAdr(content, filename, adrNumbers) {
  const violations = [];
  const warnings = [];
  const { adr } = RULES;

  if (!adr.filenamePattern.test(filename)) {
    violations.push({
      rule: '文件名须形如 0001-short-slug.md',
      detail: `实际文件名：${filename}。四位顺序编号、连字符、小写 slug、.md；编号扫 docs/adr/ 里最大号 +1。`,
    });
  }
  const ownNumber = /^([0-9]{4})-/.exec(filename)?.[1] ?? null;

  const parsed = parseFrontmatter(content);
  if (!parsed) {
    violations.push({
      rule: '缺少 frontmatter',
      detail:
        '文件以 --- 开头，里面写 status（proposed / accepted / deprecated / superseded by ADR-NNNN）。' +
        '上游 ADR-FORMAT.md 把 Status 列为可选，本仓库要求必填：没有它无法机械判断这份 ADR 今天还有效没有。',
    });
  } else {
    for (const key of Object.keys(parsed.data)) {
      if (!adr.frontmatterKeys.includes(key)) {
        violations.push({
          rule: `frontmatter 里有自造的键：${key}`,
          detail:
            `ADR-FORMAT.md 只定义了 ${adr.frontmatterKeys.join('、')}。取代关系写在 status 里` +
            '（superseded by ADR-NNNN）；只被部分取代的仍写 accepted，在正文里说明哪几处失效。',
        });
      }
    }
    const status = parsed.data.status;
    const missing =
      status === undefined || status === '' || (Array.isArray(status) && status.length === 0);
    if (missing) {
      violations.push({
        rule: 'frontmatter 缺少必填键 status',
        detail: `取值须是 ${adr.statusValues.join(' / ')} 之一，或 superseded by ADR-NNNN。`,
      });
    } else if (
      typeof status !== 'string' ||
      (!adr.statusValues.includes(status) && !adr.supersededPattern.test(status))
    ) {
      violations.push({
        rule: `status 取值非法：${JSON.stringify(status)}`,
        detail:
          `只允许 ${adr.statusValues.join(' / ')}，或 superseded by ADR-NNNN（整份失效时用，` +
          'NNNN 是取代它的那份）。只被部分取代的仍写 accepted。',
      });
    } else {
      const target = adr.supersededPattern.exec(status)?.[1];
      if (target && !adrNumbers.has(target)) {
        violations.push({
          rule: `status 指向不存在的 ADR ${target}`,
          detail: 'docs/adr/ 里没有这个编号的文件。先写新 ADR、再改旧 ADR 指向它；删过 ADR 时要同步清理指向它的引用。',
        });
      } else if (target && ownNumber !== null && Number(target) <= Number(ownNumber)) {
        violations.push({
          rule: `status 指向的 ADR ${target} 不比本文新`,
          detail: '取代者必然是后来写的，编号应大于本文；方向写反会让读者找错文件。',
        });
      }
    }
  }

  const body = parsed ? parsed.body : content;
  const headings = [...body.matchAll(/^(#{1,6})\s+(.+?)\s*$/gm)];

  const h1 = headings.filter((match) => match[1] === '#');
  if (h1.length !== 1) {
    violations.push({
      rule: `一级标题应为 1 个，实际 ${h1.length} 个`,
      detail: '文件里只该有一个 # 标题，就是这份 ADR 的名字。',
    });
  }
  for (const match of h1) {
    if (adr.forbiddenTitlePrefix.test(match[2])) {
      violations.push({
        rule: '标题里重复写了 ADR 编号',
        detail: `标题是 ${JSON.stringify(match[2])}。编号已经在文件名里，标题写决策本身。`,
      });
    }
  }

  for (const heading of headings.filter((match) => match[1] === '##')) {
    if (!adr.allowedSections.includes(heading[2])) {
      violations.push({
        rule: `不允许的二级标题：${heading[2]}`,
        detail:
          `只允许 ${adr.allowedSections.map((name) => `## ${name}`).join(' 与 ')}，两者都可选。` +
          '背景、为什么、术语澄清写进正文段落，不另开标题。',
      });
    }
  }

  for (const target of brokenLinks(body, adr.dir)) {
    violations.push({
      rule: `链接指向不存在的文件：${target}`,
      detail: '相对链接按本文件所在目录解析。删过或改名过文件时要同步改指向它的链接。',
    });
  }

  const lineCount = content.replace(/\r\n/g, '\n').split('\n').length;
  if (lineCount > adr.warnLines) {
    warnings.push({
      rule: `篇幅 ${lineCount} 行，超过建议上限 ${adr.warnLines} 行`,
      detail: 'ADR 记的是决策与理由；施工细节、验收清单、进度叙述不属于这里。',
    });
  }

  return { violations, warnings };
}

function validateContext(content) {
  const violations = [];
  const warnings = [];
  const { context } = RULES;
  const lines = content.replace(/\r\n/g, '\n').split('\n');

  const seen = new Map();
  let current = null;
  for (let index = 0; index < lines.length; index += 1) {
    const match = context.entryPattern.exec(lines[index]);
    if (match) {
      if (current) finishEntry(current);
      current = { name: match[1], line: index + 1, definitionLines: 0 };
      const previous = seen.get(match[1]);
      if (previous !== undefined) {
        violations.push({
          rule: `词条重复：${match[1]}`,
          detail: `第 ${previous} 行与第 ${index + 1} 行重复定义同一个术语。`,
        });
      }
      seen.set(match[1], index + 1);
      continue;
    }
    if (current) {
      if (lines[index].trim() === '') {
        finishEntry(current);
        current = null;
      } else {
        current.definitionLines += 1;
      }
    }
  }
  if (current) finishEntry(current);

  function finishEntry(entry) {
    if (entry.definitionLines > context.warnDefinitionLines) {
      warnings.push({
        rule: `词条「${entry.name}」（第 ${entry.line} 行）定义 ${entry.definitionLines} 行`,
        detail:
          `术语表只说「是什么」，建议 ${context.warnDefinitionLines} 行以内。` +
          '决策与取舍写进 ADR，不写进词条。',
      });
    }
  }

  for (const target of brokenLinks(content, '.')) {
    violations.push({
      rule: `链接指向不存在的文件：${target}`,
      detail: '相对链接按仓库根解析。删过或改名过文件时要同步改指向它的链接。',
    });
  }

  return { violations, warnings };
}

// ------------------------------------------------------------- 输入归一化

/**
 * 把各 harness 传来的工具调用归一化成 `[{ path, content, partial }]`。
 *
 * 参数位置在各 harness 之间不一致，先抹平：
 *   - pi：write/edit 的 path、content 在顶层；
 *   - Claude、Gemini：包在 tool_input 里，给 file_path 加 content / new_string；
 *   - Codex：文件编辑不是结构化字段，而是把整份补丁放在 tool_input.command 里，
 *     形如 `*** Add File: docs/adr/x.md` 后跟 `+行`。不解析它的话，这个 harness 上的
 *     门禁会静默放行一切——装了等于没装。
 *
 * 认不出的形状返回空数组，调用方按通过处理——门禁是提醒，不是安全边界（bash 重定向本来就能
 * 绕过任何 write/edit 闸门），不该因为在某个 harness 上认不出字段就挡住正常开发。
 */
function normalizeHookInput(rawInput, repoRoot) {
  const input =
    rawInput && typeof rawInput.tool_input === 'object' && rawInput.tool_input !== null
      ? { ...rawInput, ...rawInput.tool_input }
      : rawInput;
  if (!input || typeof input !== 'object') return [];

  // 1、Codex 的补丁格式：一份 command 里可能改多个文件，先拆成逐文件片段。
  if (typeof input.command === 'string' && input.command.includes('*** ')) {
    return parsePatch(input.command, repoRoot);
  }

  const rawPath = input.path ?? input.file_path ?? input.filePath;
  if (typeof rawPath !== 'string' || rawPath === '') return [];

  const absolute = resolve(repoRoot, rawPath);
  const rel = relative(repoRoot, absolute);
  if (rel.startsWith('..') || rel.split(sep)[0] === '..') return [];

  // 2、整体写入：content / file_text 直接就是新内容。
  if (typeof input.content === 'string') return [{ path: rel, content: input.content }];
  if (typeof input.file_text === 'string') return [{ path: rel, content: input.file_text }];

  // 3、pi 风格的多段编辑：[{ oldText, newText }]
  const rawEdits = input.edits;
  if (Array.isArray(rawEdits) && rawEdits.length > 0) {
    if (!existsSync(absolute)) return [];
    let text = readFileSync(absolute, 'utf8');
    for (const edit of rawEdits) {
      const from = edit.oldText ?? edit.old_string;
      const to = edit.newText ?? edit.new_string;
      if (typeof from !== 'string' || typeof to !== 'string') return [];
      if (!text.includes(from)) return []; // 匹配不上时无法预演，交给工具自己报错
      text = text.replace(from, to);
    }
    return [{ path: rel, content: text }];
  }

  // 4、Claude 风格的单段编辑。
  if (typeof input.old_string === 'string' && typeof input.new_string === 'string') {
    if (!existsSync(absolute)) return [];
    const text = readFileSync(absolute, 'utf8');
    if (!text.includes(input.old_string)) return [];
    return [{ path: rel, content: text.replace(input.old_string, input.new_string) }];
  }

  return [];
}

/**
 * 解析 Codex 的 `apply_patch` 补丁。
 *
 * 每个文件片段标 partial：修改现有文件时补丁只有片段，看不到全文，无法判断 frontmatter 是否
 * 存在，只能检查「本次新增的内容有没有引入违规标题」。这足以拦住「顺手加一个自造章节」，
 * 又不会因为看不到上下文而误拦。新建文件（`*** Add File:`）拿到的是全文，按全文校验。
 */
function parsePatch(command, repoRoot) {
  const targets = [];
  let current = null;
  for (const line of command.split(/\r?\n/)) {
    const addFile = /^\*\*\* Add File: (.+)$/.exec(line);
    const updateFile = /^\*\*\* Update File: (.+)$/.exec(line);
    if (addFile || updateFile) {
      const rawPath = (addFile ?? updateFile)[1].trim();
      const rel = relative(repoRoot, resolve(repoRoot, rawPath));
      if (rel.startsWith('..') || rel.split(sep)[0] === '..') {
        current = null;
        continue;
      }
      current = { path: rel, lines: [], isNew: Boolean(addFile) };
      targets.push(current);
      continue;
    }
    if (/^\*\*\* (End Patch|Delete File)/.test(line)) {
      current = null;
      continue;
    }
    if (current && line.startsWith('+')) current.lines.push(line.slice(1));
  }

  return targets
    .filter((target) => target.lines.length > 0)
    .map((target) => ({
      path: target.path,
      content: target.lines.join('\n'),
      partial: !target.isNew,
    }));
}

/** 这个路径属于本门禁管的范围吗。 */
function classify(rel) {
  const normalized = rel.split(sep).join('/');
  if (normalized === RULES.context.file) return { kind: 'context', filename: RULES.context.file };
  const adrPrefix = `${RULES.adr.dir}/`;
  if (normalized.startsWith(adrPrefix)) {
    const filename = normalized.slice(adrPrefix.length);
    if (filename.includes('/')) return null;
    if (!filename.endsWith('.md')) return null;
    return { kind: 'adr', filename };
  }
  return null;
}

function checkContent(content, kind, filename, adrNumbers) {
  return kind === 'adr' ? validateAdr(content, filename, adrNumbers) : validateContext(content);
}

// ---------------------------------------------------------------- 拒绝文案

/** 路径统一用正斜杠展示，避免 Windows 上出现反斜杠。 */
function displayPath(rel) {
  return rel.split(sep).join('/');
}

/**
 * 违规时回给模型的话。
 *
 * 关键是**别让它纳闷**：说清违反了哪条规则、这是谁定的门禁、下一步该做什么。只报「格式错误」会
 * 让模型反复试错，而它真正该做的是去读 skill。
 */
function renderBlocked(target, findings, addedOnly, preExistingCount = 0) {
  const lines = [];
  lines.push(
    addedOnly
      ? `写入 ${displayPath(target)} 被拒绝：本次修改新增了 ${findings.violations.length} 处不合规。`
      : `写入 ${displayPath(target)} 被拒绝：${findings.violations.length} 处不合规。`,
  );
  lines.push('');
  if (addedOnly && preExistingCount > 0) {
    lines.push(
      `（这个文件本来就有 ${preExistingCount} 处不合规，不在本次拦截范围内：` +
        '门禁只拦「让文件变差」，不拦修复过程中的中间状态。）',
    );
    lines.push('');
  }
  for (const item of findings.violations) {
    lines.push(`· ${item.rule}`);
    if (item.detail) {
      for (const detailLine of wrap(item.detail, 76)) lines.push(`  ${detailLine}`);
    }
  }
  lines.push('');
  lines.push('这是仓库门禁（根 AGENTS.md「仓库约定」）：CONTEXT.md 与 docs/adr/ 只经 domain-modeling');
  lines.push('skill 写入，格式以 skill 目录里的上游原文为准。先读这三个文件，再按它们改：');
  lines.push('');
  lines.push(`  ${SKILL_DIR}/SKILL.md          （判据：什么样的决策才值得写）`);
  lines.push(`  ${SKILL_DIR}/ADR-FORMAT.md     （ADR 写成什么样）`);
  lines.push(`  ${SKILL_DIR}/CONTEXT-FORMAT.md （术语表写成什么样）`);
  lines.push('');
  lines.push('不要猜格式重试，也不要绕过门禁。本仓库比上游多要求的只有「status 必填」一条。');
  return lines.join('\n');
}

function wrap(text, width) {
  // 中文没有词边界，按字符断行；但不能让标点落在行首（中文排版禁忌），
  // 所以断行前把行尾的标点退回下一行。
  const noLineStart = '，。、；：）】》”！？…—';
  const out = [];
  let line = '';
  for (const char of text) {
    if (line.length >= width && !noLineStart.includes(char)) {
      out.push(line);
      line = '';
    }
    line += char;
  }
  if (line) out.push(line);
  return out;
}

// ------------------------------------------------------------------ 三种模式

function runFiles(paths, adrNumbers, root = REPO_ROOT) {
  let violations = 0;
  let warnings = 0;
  for (const target of paths) {
    const rel = relative(root, resolve(root, target)).split(sep).join('/');
    const kind = classify(rel);
    if (!kind) continue;
    const absolute = join(root, rel);
    if (!existsSync(absolute) || !statSync(absolute).isFile()) continue;
    const findings = checkContent(readFileSync(absolute, 'utf8'), kind.kind, kind.filename, adrNumbers);
    violations += findings.violations.length;
    warnings += findings.warnings.length;
    for (const item of findings.violations) {
      console.log(`✗ ${displayPath(rel)}`);
      console.log(`  ${item.rule}`);
      if (item.detail) console.log(`  ${item.detail}`);
    }
    for (const item of findings.warnings) {
      console.log(`! ${displayPath(rel)}`);
      console.log(`  ${item.rule}`);
    }
  }
  return { violations, warnings };
}

function runAll(adrNumbers) {
  const targets = [RULES.context.file];
  const adrDir = join(REPO_ROOT, RULES.adr.dir);
  if (existsSync(adrDir)) {
    for (const name of readdirSync(adrDir).sort()) {
      if (name.endsWith('.md')) targets.push(`${RULES.adr.dir}/${name}`);
    }
  }
  return runFiles(targets, adrNumbers);
}

function runStdin(adrNumbers) {
  let raw = '';
  try {
    raw = readFileSync(0, 'utf8');
  } catch {
    return 0; // 没有 stdin 内容，无事可做
  }
  if (raw.trim() === '') return 0;

  let input;
  try {
    input = JSON.parse(raw);
  } catch {
    return 0; // 认不出就不阻塞
  }

  const normalized = normalizeHookInput(input, REPO_ROOT);
  if (normalized.length === 0) return 0;

  let blocked = 0;
  for (const target of normalized) {
    const kind = classify(target.path);
    if (!kind) continue;
    const outcome = checkOne(target, kind, adrNumbers);
    if (outcome !== 0) blocked = outcome;
  }
  return blocked;
}

/** 校验一个文件片段。返回 0 放行，2 拦截。 */
function checkOne(target, kind, adrNumbers) {
  // partial（Codex 的补丁片段）只有新增行，看不到全文：frontmatter 是否在、一级标题有几个，
  // 这些都要看全文才能判，所以只检查「新增内容里有没有违规的标题」。
  const findings = target.partial
    ? { violations: partialViolations(target.content), warnings: [] }
    : checkContent(target.content, kind.kind, kind.filename, adrNumbers);

  // 已存在的文件按「不允许变得更差」判定：只拦**新增**的违规。修一份不合规的旧文件时，
  // 中间状态可能仍不合规；若要求一次写对，门禁就会拦住修复本身——那正是最让模型纳闷的情形。
  // 新文件没有旧版，全部违规都算新增。
  const absolute = join(REPO_ROOT, target.path);
  let newViolations = findings.violations;
  let addedOnly = false;
  let preExistingCount = 0;
  const existingWarningRules = [];
  if (!target.partial && existsSync(absolute) && statSync(absolute).isFile()) {
    const before = checkContent(readFileSync(absolute, 'utf8'), kind.kind, kind.filename, adrNumbers);
    const known = new Set(before.violations.map((item) => item.rule));
    newViolations = findings.violations.filter((item) => !known.has(item.rule));
    addedOnly = true;
    preExistingCount = before.violations.length;
    for (const item of before.warnings) existingWarningRules.push(item.rule);
  }

  const knownWarnings = new Set(existingWarningRules);
  for (const item of findings.warnings) {
    if (!knownWarnings.has(item.rule)) {
      console.error(`! ${displayPath(target.path)}：${item.rule}`);
    }
  }
  if (newViolations.length === 0) return 0;

  const message = renderBlocked(target.path, { violations: newViolations }, addedOnly, preExistingCount);
  // 唯一的输出契约：退出 2 + 理由写 stderr。理由必须真的到达模型——它看不到就只会盲试。
  console.error(message);
  return 2;
}

/** 只有片段时能做的检查：新增内容里不能出现标题。 */
function partialViolations(content) {
  const violations = [];
  for (const match of content.matchAll(/^(#{1,6})\s+(.+?)\s*$/gm)) {
    const level = match[1].length;
    violations.push({
      rule: `${level} 级标题：${match[2]}`,
      detail:
        level === 2
          ? `只允许 ${RULES.adr.allowedSections.map((name) => `## ${name}`).join(' 与 ')}，两者都可选。背景、为什么、术语澄清写进正文段落。`
          : 'ADR 全文只该有一个 # 标题（就是这份 ADR 的名字）。',
    });
  }
  return violations;
}

// ---------------------------------------------------------------------- 入口

function main() {
  const args = process.argv.slice(2);
  const stdinMode = args.includes('--stdin');
  const filesIndex = args.indexOf('--files');

  const adrNumbers = existingAdrNumbers();

  if (stdinMode) {
    return runStdin(adrNumbers);
  }
  if (filesIndex >= 0) {
    // --root 给 git hook 用：它把暂存内容取到临时目录再校验，规则与仓库一致，
    // 但文件不在仓库里。
    const rootIndex = args.indexOf('--root');
    const root = rootIndex >= 0 ? resolve(args[rootIndex + 1]) : REPO_ROOT;
    const { violations } = runFiles(args.slice(filesIndex + 1), adrNumbers, root);
    return violations > 0 ? 1 : 0;
  }
  const { violations, warnings } = runAll(adrNumbers);
  console.log('');
  console.log(`检查完毕：${violations} 处违规，${warnings} 处提醒。`);
  return violations > 0 ? 1 : 0;
}

try {
  process.exit(main());
} catch (error) {
  console.error(`门禁脚本自身出错：${error.message}`);
  process.exit(3);
}
