import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PRECOMPILED_CACHE_PATH = path.resolve(__dirname, '.compiled_cache.json');

function findBabelCore() {
  if (process.env.FORCE_PORTABLE_CACHE === '1') return null;
  const candidates = [
    '@babel/core',
    '/usr/share/nodejs/@babel/core',
    path.resolve(process.cwd(), 'node_modules/@babel/core'),
  ];
  for (const candidate of candidates) {
    try {
      return require(candidate);
    } catch {
      // continue
    }
  }
  return null;
}

const babel = findBabelCore();
let precompiledDiskCache = {};
if (fs.existsSync(PRECOMPILED_CACHE_PATH)) {
  try {
    precompiledDiskCache = JSON.parse(fs.readFileSync(PRECOMPILED_CACHE_PATH, 'utf8'));
  } catch {
    precompiledDiskCache = {};
  }
}

export function transpileTsToCjs(src, filename) {
  const relKey = path.relative(path.resolve(__dirname, '..'), filename).replace(/\\/g, '/');
  if (babel) {
    try {
      const presets = [];
      for (const p of ['@babel/preset-typescript', '/usr/share/nodejs/@babel/preset-typescript']) {
        try {
          presets.push(require.resolve(p));
          break;
        } catch {}
      }
      const plugins = [];
      for (const pl of ['@babel/plugin-transform-modules-commonjs', '/usr/share/nodejs/@babel/plugin-transform-modules-commonjs']) {
        try {
          plugins.push(require.resolve(pl));
          break;
        } catch {}
      }
      if (presets.length > 0 && plugins.length > 0) {
        const code = babel.transformSync(src, { filename, presets, plugins }).code;
        precompiledDiskCache[relKey] = code;
        try {
          fs.writeFileSync(PRECOMPILED_CACHE_PATH, JSON.stringify(precompiledDiskCache, null, 2));
        } catch {}
        return code;
      }
    } catch {
      // fallback to precompiled cache
    }
  }
  if (precompiledDiskCache[relKey]) {
    return precompiledDiskCache[relKey];
  }
  throw new Error(`Cannot transpile ${relKey}: @babel/core not installed and no precompiled cache entry found.`);
}

const codeCache = new Map();

export function loadTsModule(filePath) {
  const absPath = path.resolve(filePath);
  const moduleCache = new Map();

  function loadInternal(targetPath) {
    const resolved = path.resolve(targetPath);
    if (moduleCache.has(resolved)) {
      return moduleCache.get(resolved).exports;
    }
    let code = codeCache.get(resolved);
    if (!code) {
      const src = fs.readFileSync(resolved, 'utf8');
      code = transpileTsToCjs(src, resolved);
      codeCache.set(resolved, code);
    }
    const mod = { exports: {} };
    moduleCache.set(resolved, mod);

    const customRequire = (id) => {
      if (id.startsWith('.')) {
        const baseResolved = path.resolve(path.dirname(resolved), id);
        for (const candidate of [baseResolved, `${baseResolved}.ts`, `${baseResolved}.tsx`, `${baseResolved}.js`, `${baseResolved}.json`]) {
          if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) {
            if (candidate.endsWith('.json')) {
              return JSON.parse(fs.readFileSync(candidate, 'utf8'));
            }
            return loadInternal(candidate);
          }
        }
      }
      return require(id);
    };

    const fn = new Function('exports', 'require', 'module', '__filename', '__dirname', code);
    fn(mod.exports, customRequire, mod, resolved, path.dirname(resolved));
    return mod.exports;
  }

  return loadInternal(absPath);
}
