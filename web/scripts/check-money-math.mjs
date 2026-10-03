import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const sourceRoot = join(dirname(fileURLToPath(import.meta.url)), "..", "src");
const moneyName = /(?:amount|grand[_-]?total|taxable|payable|variance|discount|currency|cgst|sgst|igst)/i;
const arithmeticKinds = new Set([
  ts.SyntaxKind.PlusToken,
  ts.SyntaxKind.MinusToken,
  ts.SyntaxKind.AsteriskToken,
  ts.SyntaxKind.SlashToken,
  ts.SyntaxKind.PercentToken,
  ts.SyntaxKind.PlusEqualsToken,
  ts.SyntaxKind.MinusEqualsToken,
  ts.SyntaxKind.AsteriskEqualsToken,
  ts.SyntaxKind.SlashEqualsToken,
]);
const numericConverters = new Set(["Number", "parseFloat", "parseInt"]);

function listSourceFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return listSourceFiles(path);
    return /\.tsx?$/.test(entry.name) && !entry.name.endsWith(".d.ts") ? [path] : [];
  });
}

function describesMoney(node) {
  if (ts.isIdentifier(node) && moneyName.test(node.text)) return true;
  if (ts.isPropertyAccessExpression(node) && moneyName.test(node.name.text)) return true;
  if (ts.isElementAccessExpression(node) && node.argumentExpression && ts.isStringLiteralLike(node.argumentExpression) && moneyName.test(node.argumentExpression.text)) return true;
  let found = false;
  ts.forEachChild(node, (child) => {
    if (describesMoney(child)) {
      found = true;
      return true;
    }
    return undefined;
  });
  return found;
}

const violations = [];
for (const path of listSourceFiles(sourceRoot)) {
  const source = ts.createSourceFile(path, readFileSync(path, "utf8"), ts.ScriptTarget.Latest, true, path.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  function visit(node) {
    if (ts.isBinaryExpression(node) && arithmeticKinds.has(node.operatorToken.kind) && (describesMoney(node.left) || describesMoney(node.right))) {
      const location = source.getLineAndCharacterOfPosition(node.getStart(source));
      violations.push(`${relative(sourceRoot, path)}:${location.line + 1}: arithmetic expression references money data`);
    }
    if (ts.isPrefixUnaryExpression(node) && (node.operator === ts.SyntaxKind.PlusToken || node.operator === ts.SyntaxKind.MinusToken) && describesMoney(node.operand)) {
      const location = source.getLineAndCharacterOfPosition(node.getStart(source));
      violations.push(`${relative(sourceRoot, path)}:${location.line + 1}: unary arithmetic expression references money data`);
    }
    if (ts.isCallExpression(node)) {
      const callee = ts.isIdentifier(node.expression) ? node.expression.text : "";
      if (numericConverters.has(callee) && node.arguments.some(describesMoney)) {
        const location = source.getLineAndCharacterOfPosition(node.getStart(source));
        violations.push(`${relative(sourceRoot, path)}:${location.line + 1}: numeric conversion references money data`);
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(source);
}

if (violations.length) {
  process.stderr.write(`Client-side money arithmetic is forbidden:\n${violations.join("\n")}\n`);
  process.exitCode = 1;
} else {
  process.stdout.write("Money safety check passed: no client arithmetic or numeric conversion references monetary fields.\n");
}
