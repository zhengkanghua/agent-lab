#!/usr/bin/env node
/**
 * 领域模型文档的结构门禁。
 *
 * 它执行的是 `.codex/skills/domain-modeling/FORMAT-CONTRACT.md` 里的规则——**规则不在这里**，
 * 这里只是把那份契约跑起来。改规则改契约文件，四个 harness 的 hook 与 git hook 一起生效；
 * 本文件里的逻辑只负责「怎么读内容、怎么比对」。两份定义会漂移，所以只留一份。
 *
 * 为什么是结构校验而不是「检查有没有调用过 skill」：调用记录拦不住真正的失败模式——模型完全
 * 可以不调 skill 而写出一份格式对、判据错的 ADR。写出来的东西合不合规是确定的，可以机械判断。
 *
 * 用法：
 *   node scripts/check-domain-docs.mjs                  # 全量检查，报告打到 stdout；有违规退出 1
 *   node scripts/check-domain-docs.mjs --stdin          # hook 模式：从 stdin 读一次工具调用
 *   node scripts/check-domain-docs.mjs --files a.md b.md # 只检查指定文件
 *   node scripts/check-domain-docs.mjs --files a.md --root /tmp/x  # 校验仓库外的一份拷贝（git hook 用）
 *
 * hook 模式对**已存在的文件**按「不允许变得更差」判定：只拦本次新增的违规，不拦修复过程中的
 * 中间状态（存量 ADR 的修复必然要经过「还不完全合规」的中间态）。新文件没有旧版，全部违规都算新增。
 *
 * hook 模式的输出契约只有一条：**违规时退出 2、把理由写到 stderr**。这是四个 harness 都支持的
 * 最小公共面——Claude、Codex、Gemini 的文档都写明「退出码 2 即拦截，stderr 作为拒绝理由回给模型」，
 * pi 的扩展自己读 stderr。曾经想为每个 harness 分别输出结构化 JSON（Codex/Claude 要
 * hookSpecificOutput.permissionDecision、Gemini 要 decision），那会让某些 harness 拦住了却不告诉模型
 * 原因，而模型看不到理由就只会盲试。一种机制比四种兼容层可靠。
 *
 * hook 模式读的 JSON（字段名按 harness 归一化，两种拼法都认；Claude/Codex/Gemini 的参数
 * 嵌在 tool_input 里，pi 的在顶层，脚本先拆一层）：
 *   { "path"|"file_path": "docs/adr/00xx-x.md",
 *     "content"|"file_text": "…",                       // 整体写入
 *     "edits": [{"oldText"|"old_string", "newText"|"new_string"}],  // 增量编辑
 *     "old_string", "new_string" }                      // 单段编辑
 *
 * 退出码：0 通过（可能有警告）；1 有违规（全量模式）；2 有违规（hook 模式，Claude/Codex/Gemini
 * 用 2 表示拦截）；3 脚本自身出错。**认不出的输入形状按通过处理**（不阻塞开发），真正的兜底是
 * git hook 对落盘文件的检查。
 */

import { readFileSync, readdirSync, existsSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const CONTRACT_PATH = '.codex/skills/domain-modeling/FORMAT-CONTRACT.md';
const SKILL_DIR = '.codex/skills/domain-modeling';

// ---------------------------------------------------------------- 契约解析

/**
 * 从契约文件里取出那个 ```json 块。
 *
 * 只要第一个 JSON 代码块：契约文件本身也讲规则，后面可能再出现示例代码块。
 */
function loadContract() {
  const text = readFileSync(join(REPO_ROOT, CONTRACT_PATH), 'utf8');
  const blocks = [...text.matchAll(/```json\n([\s\S]*?)```/g)];
  if (blocks.length === 0) {
    throw new Error(`契约文件里找不到 json 代码块：${CONTRACT_PATH}`);
  }
  const contract = JSON.parse(blocks[0][1]);
  if (contract.version !== 1) {
    throw new Error(`契约版本不认识：${contract.version}`);
  }
  return contract;
}

// ------------------------------------------------------------ 极简 YAML 子集

/**
 * 只解析 frontmatter 用得到的 YAML 子集：`key: value`、`key: [a, b]`、以及
 * `key:` 后跟 `- item` 行组成的列表。项目里 ADR frontmatter 只有 status 与 superseded-by
 * 两个键，为它拉一个 yaml 依赖不值得。
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

/** 收集目录里已存在的 ADR 编号，用于交叉校验 superseded-by 不悬空。 */
function existingAdrNumbers(adrDir) {
  const dir = join(REPO_ROOT, adrDir);
  if (!existsSync(dir)) return new Set();
  const numbers = new Set();
  for (const name of readdirSync(dir)) {
    const match = /^([0-9]{4})-/.exec(name);
    if (match && name.endsWith('.md')) numbers.add(match[1]);
  }
  return numbers;
}

/** 归一化编号写法：接受 `0031`、`ADR 0031`、`adr/0031-foo.md` 三种。 */
function normalizeAdrRef(value) {
  const match = /([0-9]{4})/.exec(String(value));
  return match ? match[1] : null;
}

function validateAdr(content, filename, contract, adrNumbers) {
  const violations = [];
  const warnings = [];
  const { adr } = contract;

  if (!new RegExp(adr.filenamePattern).test(filename)) {
    violations.push({
      rule: `文件名须匹配 ${adr.filenamePattern}`,
      detail: `实际文件名：${filename}。形如 0031-short-slug.md，slug 用连字符小写。`,
    });
  }

  const parsed = parseFrontmatter(content);
  if (!parsed) {
    violations.push({
      rule: '缺少 frontmatter',
      detail:
        '文件必须以 --- 开头，里面至少写 status（accepted / proposed / deprecated / superseded）。' +
        '没有它，读者无法判断这份 ADR 今天是否还有效——现有 26 份 ADR 里 0 份用了 frontmatter，' +
        '取而代之的是划线、追加节、横幅四种互不兼容的写法，那是本次要收敛掉的东西。',
    });
  } else {
    for (const key of adr.frontmatter.required) {
      if (!(key in parsed.data) || parsed.data[key] === '' || parsed.data[key].length === 0) {
        violations.push({
          rule: `frontmatter 缺少必填键 ${key}`,
          detail: `取值须是 ${adr.frontmatter.statusValues.join(' / ')} 之一。`,
        });
      }
    }
    const status = parsed.data.status;
    if (status !== undefined && !adr.frontmatter.statusValues.includes(status)) {
      violations.push({
        rule: `status 取值非法：${JSON.stringify(status)}`,
        detail: `只允许 ${adr.frontmatter.statusValues.join(' / ')}。部分被取代仍然写 accepted，` +
          `另用 superseded-by 列出取代它的编号。`,
      });
    }
    // superseded-by 是被取代指向，不是状态：它列出的每个编号都必须真实存在。
    // 键名、必填性都来自契约（optional 里的每一项当存在时校验），不在这里再写死一个词。
    for (const key of adr.frontmatter.optional) {
      const value = parsed.data[key];
      if (value === undefined) continue;
      if (!Array.isArray(value)) {
        violations.push({
          rule: `frontmatter 的 ${key} 应是编号数组`,
          detail: `写法如 ${key}: [0027, 0029]，或分行写 - 0027。`,
        });
        continue;
      }
      for (const ref of value) {
        const number = normalizeAdrRef(ref);
        if (!number) {
          violations.push({ rule: `${key} 里的 ${JSON.stringify(ref)} 认不出编号`, detail: '' });
        } else if (!adrNumbers.has(number)) {
          violations.push({
            rule: `${key} 指向不存在的 ADR ${number}`,
            detail: '这份编号在 docs/adr/ 里没有对应文件。删过 ADR 时要同步清理指向它的引用。',
          });
        }
      }
    }
  }

  const body = parsed ? parsed.body : content;
  const headings = [...body.matchAll(/^(#{1,6})\s+(.+?)\s*$/gm)];

  const h1 = headings.filter((match) => match[1] === '#');
  if (h1.length !== adr.title.h1Count) {
    violations.push({
      rule: `一级标题应为 ${adr.title.h1Count} 个，实际 ${h1.length} 个`,
      detail: '文件里只该有一个 # 标题，就是这份 ADR 的名字。',
    });
  }
  for (const match of h1) {
    if (new RegExp(adr.title.forbiddenPrefixPattern).test(match[2])) {
      violations.push({
        rule: '标题里重复写了 ADR 编号',
        detail: `标题是 ${JSON.stringify(match[2])}。编号已经在文件名里，标题写决策本身。`,
      });
    }
  }

  for (const heading of headings.filter((match) => match[1] === '##')) {
    if (!adr.sections.allowed.includes(heading[2])) {
      violations.push({
        rule: `不允许的二级标题：${heading[2]}`,
        detail:
          `只允许 ${adr.sections.allowed.map((name) => `## ${name}`).join(' 与 ')}，两者都可选。` +
          '背景、为什么、术语澄清写进正文段落，不另开标题——自造标题正是「同一套节名三种写法」的来源。',
      });
    }
  }

  const lineCount = content.replace(/\r\n/g, '\n').split('\n').length;
  if (lineCount > adr.limits.warnLines) {
    warnings.push({
      rule: `篇幅 ${lineCount} 行，超过建议上限 ${adr.limits.warnLines} 行`,
      detail: 'ADR 记的是决策与理由；施工细节、验收清单、进度叙述不属于这里。',
    });
  }

  return { violations, warnings };
}

function validateContext(content, contract) {
  const violations = [];
  const warnings = [];
  const { context } = contract;
  const entryRe = new RegExp(context.entryPattern);
  const lines = content.replace(/\r\n/g, '\n').split('\n');

  const seen = new Map();
  let current = null;
  for (let index = 0; index < lines.length; index += 1) {
    const match = entryRe.exec(lines[index]);
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
    if (entry.definitionLines > context.limits.warnDefinitionLines) {
      warnings.push({
        rule: `词条「${entry.name}」（第 ${entry.line} 行）定义 ${entry.definitionLines} 行`,
        detail:
          `术语表只说「是什么」，建议 ${context.limits.warnDefinitionLines} 行以内。` +
          '决策与取舍写进 ADR，不写进词条。',
      });
    }
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
function classify(rel, contract) {
  const normalized = rel.split(sep).join('/');
  if (normalized === contract.context.file) return { kind: 'context', filename: 'CONTEXT.md' };
  const adrPrefix = `${contract.adr.dir}/`;
  if (normalized.startsWith(adrPrefix)) {
    const filename = normalized.slice(adrPrefix.length);
    if (filename.includes('/')) return null;
    if (!filename.endsWith('.md')) return null;
    return { kind: 'adr', filename };
  }
  return null;
}

function checkContent(content, kind, filename, contract, adrNumbers) {
  return kind === 'adr'
    ? validateAdr(content, filename, contract, adrNumbers)
    : validateContext(content, contract);
}

// ---------------------------------------------------------------- 拒绝文案

/**
 * 违规时回给模型的话。
 *
 * 关键是**别让它纳闷**：说清违反了哪条规则、这是谁定的门禁、下一步该做什么。只报「格式错误」会
 * 让模型反复试错，而它真正该做的是去读 skill。
 */
/** 路径统一用正斜杠展示，避免 Windows 上出现反斜杠。 */
function displayPath(rel) {
  return rel.split(sep).join('/');
}

function renderBlocked(target, findings, contract, addedOnly, preExistingCount = 0) {
  const lines = [];
  lines.push(
    addedOnly
      ? `写入 ${displayPath(target)} 被拒绝：本次修改新增了 ${findings.violations.length} 处不合规。`
      : `写入 ${displayPath(target)} 被拒绝：${findings.violations.length} 处不合规。`,
  );
  lines.push('');
  if (addedOnly && preExistingCount > 0) {
    lines.push(
      `（这个文件本来就有 ${preExistingCount} 处不合规，不在本次拦截范围内——存量正在逐份修复。`,
    );
    lines.push('门禁只拦「让文件变差」，不拦修复过程中的中间状态。）');
    lines.push('');
  }
  for (const item of findings.violations) {
    lines.push(`· ${item.rule}`);
    if (item.detail) {
      for (const detailLine of wrap(item.detail, 76)) lines.push(`  ${detailLine}`);
    }
  }
  lines.push('');
  lines.push('这是仓库门禁（根 AGENTS.md「仓库约定」第 4 条）：写入 CONTEXT.md 或');
  lines.push('docs/adr/ 之前必须先读格式契约，不能凭印象写。请先读这两个文件：');
  lines.push('');
  lines.push(`  ${SKILL_DIR}/SKILL.md          （判据：什么样的决策才值得写）`);
  lines.push(`  ${SKILL_DIR}/FORMAT-CONTRACT.md （格式：写成什么样算合规）`);
  lines.push('');
  lines.push('按契约改完再提交；规则本身要改，改契约文件，不要绕过门禁。');
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

function runFiles(paths, contract, adrNumbers, root = REPO_ROOT) {
  let violations = 0;
  let warnings = 0;
  for (const target of paths) {
    const rel = relative(root, resolve(root, target)).split(sep).join('/');
    const kind = classify(rel, contract);
    if (!kind) continue;
    const absolute = join(root, rel);
    if (!existsSync(absolute) || !statSync(absolute).isFile()) continue;
    const findings = checkContent(
      readFileSync(absolute, 'utf8'),
      kind.kind,
      kind.filename,
      contract,
      adrNumbers,
    );
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

function runAll(contract, adrNumbers) {
  const targets = [contract.context.file];
  const adrDir = join(REPO_ROOT, contract.adr.dir);
  if (existsSync(adrDir)) {
    for (const name of readdirSync(adrDir).sort()) {
      if (name.endsWith('.md')) targets.push(`${contract.adr.dir}/${name}`);
    }
  }
  return runFiles(targets, contract, adrNumbers);
}

function runStdin(contract, adrNumbers) {
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
    const kind = classify(target.path, contract);
    if (!kind) continue;
    const outcome = checkOne(target, kind, contract, adrNumbers);
    if (outcome !== 0) blocked = outcome;
  }
  return blocked;
}

/** 校验一个文件片段。返回 0 放行，2 拦截。 */
function checkOne(target, kind, contract, adrNumbers) {
  // partial（Codex 的补丁片段）只有新增行，看不到全文：frontmatter 是否在、一级标题有几个，
  // 这些都要看全文才能判，所以只检查「新增内容里有没有违规的标题」。
  const findings = target.partial
    ? { violations: partialViolations(target.content, contract), warnings: [] }
    : checkContent(target.content, kind.kind, kind.filename, contract, adrNumbers);

  // 已存在的文件按「不允许变得更差」判定：只拦**新增**的违规。
  //
  // 这一条是必须的：存量 26 份 ADR 里 70 处不合规，逐份修复过程必然经历「中间状态仍不合规」。
  // 若要求一次写对，门禁就会拦住修复本身——而那正是最让模型纳闷的情形：它在改好，却被拦。
  // 所以判据是「别让文件变差」，不是「必须一步到位」。新文件没有旧版，全部违规都算新增。
  const absolute = join(REPO_ROOT, target.path);
  let newViolations = findings.violations;
  let addedOnly = false;
  let preExistingCount = 0;
  const existingWarningRules = [];
  if (!target.partial && existsSync(absolute) && statSync(absolute).isFile()) {
    const before = checkContent(
      readFileSync(absolute, 'utf8'),
      kind.kind,
      kind.filename,
      contract,
      adrNumbers,
    );
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

  const message = renderBlocked(
    target.path,
    { violations: newViolations },
    contract,
    addedOnly,
    preExistingCount,
  );
  // 唯一的输出契约：退出 2 + 理由写 stderr。理由必须真的到达模型——它看不到就只会盲试。
  console.error(message);
  return 2;
}

/** 只有片段时能做的检查：新增内容里不能出现标题。 */
function partialViolations(content, contract) {
  const violations = [];
  for (const match of content.matchAll(/^(#{1,6})\s+(.+?)\s*$/gm)) {
    const level = match[1].length;
    violations.push({
      rule: `${level} 级标题：${match[2]}`,
      detail:
        level === 2
          ? `只允许 ${contract.adr.sections.allowed.map((name) => `## ${name}`).join(' 与 ')}，两者都可选。背景、为什么、术语澄清写进正文段落。`
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

  const contract = loadContract();
  const adrNumbers = existingAdrNumbers(contract.adr.dir);

  if (stdinMode) {
    return runStdin(contract, adrNumbers);
  }
  if (filesIndex >= 0) {
    // --root 给 git hook 用：它把暂存内容取到临时目录再校验，规则与仓库一致，
    // 但文件不在仓库里。契约里的相对路径仍按 root 解析。
    const rootIndex = args.indexOf('--root');
    const root = rootIndex >= 0 ? resolve(args[rootIndex + 1]) : REPO_ROOT;
    const { violations } = runFiles(args.slice(filesIndex + 1), contract, adrNumbers, root);
    return violations > 0 ? 1 : 0;
  }
  const { violations, warnings } = runAll(contract, adrNumbers);
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
