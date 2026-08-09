// Markdown 渲染工具（marked + highlight.js）：Agent 答复区富文本展示
// 样式（highlight.js github.css）在 main.js 统一导入，这里只引库不引 css
import { marked } from 'marked'
import hljs from 'highlight.js'

// marked v12+ 移除了内置 highlight 选项，改用自定义 code renderer 做代码高亮
const renderer = {
  code(code, infostring) {
    const lang = (infostring || '').match(/\S*/)?.[0] || ''
    let html = ''
    if (lang && hljs.getLanguage(lang)) {
      try {
        html = hljs.highlight(code, { language: lang }).value
      } catch (e) {
        html = hljs.highlightAuto(code).value
      }
    } else {
      try {
        html = hljs.highlightAuto(code).value
      } catch (e) {
        html = code.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      }
    }
    return `<pre><code class="hljs language-${lang}">${html}</code></pre>`
  }
}
marked.use({ renderer })

marked.setOptions({
  gfm: true,
  breaks: true
})

/** 渲染 markdown 为 HTML 字符串（调用方用 v-html 展示） */
export function renderMarkdown(text) {
  if (!text) return ''
  try {
    return marked.parse(text)
  } catch (e) {
    return text
  }
}

/** 纯文本长度截断（思考过程折叠摘要等） */
export function truncate(text, max = 120) {
  if (!text) return ''
  return text.length > max ? text.slice(0, max) + '…' : text
}
