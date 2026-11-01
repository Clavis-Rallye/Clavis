import { readdirSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, it, expect } from 'vitest';
import ts from 'typescript';

// Every literal key that the production code passes to a `t` function or to an
// `i18nKey` attribute must exist in German and in English. A key that one
// language lacks shows the raw key, or a German `defaultValue`, in the other.

const LANGUAGES = ['de', 'en'];
const PLURAL_SUFFIXES = ['zero', 'one', 'few', 'many', 'other'];

const locales = import.meta.glob('./locales/*/*.json', { eager: true, import: 'default' }) as Record<string, unknown>;

/** Every production `*.ts` and `*.tsx` under `src/`, keyed by its path from this folder (`../x/y.ts`), without the tests and `src/test/`. */
function readSources(): Record<string, string> {
  const root = join(import.meta.dirname, '..');
  const files: Record<string, string> = {};
  const walk = (dir: string): void => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) {
        if (full !== join(root, 'test')) walk(full);
      } else if (/\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) {
        files[`../${relative(root, full).split('\\').join('/')}`] = readFileSync(full, 'utf8');
      }
    }
  };
  walk(root);
  return files;
}
const sources = readSources();

/** A key is found when its path is a leaf or an object, or when a plural form of it exists. */
function hasKey(lang: string, ns: string, key: string): boolean {
  let node = locales[`./locales/${lang}/${ns}.json`] as Record<string, unknown> | undefined;
  const parts = key.split('.');
  for (const [i, part] of parts.entries()) {
    if (node === undefined || typeof node !== 'object') return false;
    if (part in node) {
      node = node[part] as Record<string, unknown>;
    } else {
      const isLast = i === parts.length - 1;
      return isLast && PLURAL_SUFFIXES.some((suffix) => `${part}_${suffix}` in node!);
    }
  }
  return true;
}

const text = (node?: ts.Node): string | undefined =>
  node && (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) ? node.text : undefined;

const option = (opts: ts.Node | undefined, name: string): ts.Expression | undefined =>
  opts && ts.isObjectLiteralExpression(opts)
    ? opts.properties.find((p): p is ts.PropertyAssignment => ts.isPropertyAssignment(p) && p.name.getText() === name)?.initializer
    : undefined;

interface Binding {
  namespaces: string[];
  keyPrefix: string;
}
const COMMON: Binding = { namespaces: ['common'], keyPrefix: '' };

/** The `t` functions that a file gets from `useTranslation`, by the name they are bound to. */
function findBindings(file: ts.SourceFile): Map<string, Binding> {
  const bindings = new Map<string, Binding>();
  const visit = (node: ts.Node): void => {
    if (
      ts.isVariableDeclaration(node) &&
      ts.isObjectBindingPattern(node.name) &&
      node.initializer &&
      ts.isCallExpression(node.initializer) &&
      node.initializer.expression.getText(file) === 'useTranslation'
    ) {
      const [nsArg, opts] = node.initializer.arguments;
      const namespaces = !nsArg ? ['common'] : ts.isArrayLiteralExpression(nsArg) ? nsArg.elements.map(text).filter((n) => n !== undefined) : [text(nsArg) ?? '?'];
      const keyPrefix = text(option(opts, 'keyPrefix')) ?? '';
      for (const element of node.name.elements) {
        const from = (element.propertyName ?? element.name).getText(file);
        if (from === 't') bindings.set(element.name.getText(file), { namespaces, keyPrefix });
        if (from === 'i18n') bindings.set(`${element.name.getText(file)}.t`, COMMON);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  // The global instance is not bound by a hook; its `t` reads the default namespace.
  for (const name of ['i18n.t', 'i18next.t']) if (!bindings.has(name)) bindings.set(name, COMMON);
  return bindings;
}

function findMissingKeys(): string[] {
  const missing: string[] = [];
  for (const [path, code] of Object.entries(sources)) {
    const file = ts.createSourceFile(path, code, ts.ScriptTarget.Latest, true, path.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
    const bindings = findBindings(file);

    const check = (rawKey: string, opts: ts.Node | undefined, binding: Binding, node: ts.Node, nsAttribute?: string): void => {
      const prefixed = rawKey.match(/^([a-zA-Z]+):(.+)$/);
      const key = prefixed ? prefixed[2] : binding.keyPrefix ? `${binding.keyPrefix}.${rawKey}` : rawKey;
      const own = prefixed?.[1] ?? text(option(opts, 'ns')) ?? nsAttribute;
      const namespaces = own ? [own] : binding.namespaces;
      if (namespaces.includes('?')) return; // a namespace the code computes cannot be read here
      const lng = text(option(opts, 'lng'));
      const line = file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1;
      for (const lang of lng ? [lng] : LANGUAGES) {
        if (!namespaces.some((ns) => hasKey(lang, ns, key))) missing.push(`${path.replace('../', 'src/')}:${line} ${lang} ${namespaces.join('|')}:${key}`);
      }
    };

    const visit = (node: ts.Node): void => {
      if (ts.isCallExpression(node)) {
        const binding = bindings.get(node.expression.getText(file));
        const key = text(node.arguments[0]);
        if (binding && key !== undefined) check(key, node.arguments[1], binding, node);
      }
      if (ts.isJsxAttribute(node) && node.name.getText(file) === 'i18nKey' && text(node.initializer)) {
        // `<Trans t={t} i18nKey="…" ns="…">`: the `t` prop names the function, else the first one of the file.
        const attributes = (node.parent as ts.JsxAttributes).properties;
        const attribute = (name: string) => attributes.find((a): a is ts.JsxAttribute => ts.isJsxAttribute(a) && a.name.getText(file) === name);
        const tName = attribute('t')?.initializer?.getText(file).replace(/^\{|\}$/g, '');
        const binding = (tName && bindings.get(tName)) || [...bindings.values()][0] || COMMON;
        check(text(node.initializer)!, undefined, binding, node, text(attribute('ns')?.initializer));
      }
      ts.forEachChild(node, visit);
    };
    visit(file);
  }
  return missing;
}

describe('translation keys that the code calls', () => {
  it('exist in German and in English', () => {
    expect(findMissingKeys()).toEqual([]);
  });
});
