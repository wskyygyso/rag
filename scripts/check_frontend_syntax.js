// 校验静态前端内联 JavaScript 的语法，不执行页面代码。
const fs = require("fs");
const path = require("path");

const htmlPath = path.resolve(__dirname, "..", "static", "index.html");
const html = fs.readFileSync(htmlPath, "utf8");
const match = html.match(/<script>([\s\S]*?)<\/script>/);
if (!match) {
  throw new Error("static/index.html 未找到内联 script");
}
new Function(match[1]);
console.log("inline script syntax: ok");
