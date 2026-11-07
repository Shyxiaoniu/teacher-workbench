/* 班级工作台 · H5 网页设计 · 知识库（kb.js）
   ------------------------------------------------------------
   面向初中信息科技「网页设计」单元：HTML 结构 / CSS 样式 / 布局 /
   颜色字体 / 常见效果 / 术语，共 40+ 条，每条都能一键插入代码区。

   数据形状（不要随意改字段名，studio.js 按这三个字段渲染）：
     cat  分类 id（见 CATS）
     t    条目标题
     d    一句话说明（讲清「为什么」，不只讲「是什么」）
     code 可直接插入的示例代码
     lang 'html' | 'css'（决定插到左边哪个编辑区）
   ------------------------------------------------------------ */
window.DESIGN_KB = {
  cats: [
    ['all', '全部'],
    ['html', 'HTML 结构'],
    ['text', '文字内容'],
    ['media', '图片链接'],
    ['css', '样式基础'],
    ['box', '盒子与间距'],
    ['layout', '布局排版'],
    ['color', '颜色装饰'],
    ['fx', '动效交互'],
    ['term', '名词解释']
  ],
  items: [
    /* ---------------- HTML 结构 ---------------- */
    { cat: 'html', t: '网页的三层结构', lang: 'html',
      d: 'HTML 管「有什么内容」，CSS 管「长什么样」。这个设计器里，左边 HTML 区只写 body 里的内容，文档骨架和 <style> 由平台自动补全。',
      code: '<header>我的网页</header>\n<main>\n  <h1>你好，网页世界</h1>\n  <p>这是我做的第一个网页。</p>\n</main>\n<footer>桂城一中 · 信息科技</footer>' },
    { cat: 'html', t: '标题 h1 ~ h6', lang: 'html',
      d: 'h1 最大、h6 最小。一页只用一个 h1（页面主标题），其余按层级往下用，别为了字大就用 h1。',
      code: '<h1>主标题</h1>\n<h2>小标题</h2>\n<h3>更小的标题</h3>' },
    { cat: 'html', t: '段落 p', lang: 'html',
      d: '一段话用一个 p。浏览器会自动在段落之间留出间距，不需要自己敲空行。',
      code: '<p>这是第一段话。</p>\n<p>这是第二段话。</p>' },
    { cat: 'html', t: '容器 div', lang: 'html',
      d: 'div 是一个「盒子」，本身没有样式，用来把一组内容包起来统一排版。它是网页里最常用的标签。',
      code: '<div class="card">\n  <h2>卡片标题</h2>\n  <p>卡片里的内容。</p>\n</div>' },
    { cat: 'html', t: '行内容器 span', lang: 'html',
      d: 'span 不换行，只包住一小段文字，常用来给某几个字单独上色。',
      code: '<p>我最喜欢 <span class="hl">信息科技</span> 课。</p>' },
    { cat: 'html', t: '列表 ul / ol', lang: 'html',
      d: 'ul 是无序列表（圆点），ol 是有序列表（123）。列表项一律用 li。',
      code: '<ul>\n  <li>语文</li>\n  <li>数学</li>\n  <li>信息科技</li>\n</ul>' },
    { cat: 'html', t: '链接 a', lang: 'html',
      d: 'href 写目标地址。target="_blank" 表示新标签页打开——但学生端提交的作品里，外链不会自动放行。',
      code: '<a href="https://www.example.com" target="_blank">点我打开</a>' },
    { cat: 'html', t: '语义化标签', lang: 'html',
      d: 'header / main / section / footer 和 div 长得一样，但名字说明了用途，读屏软件和搜索引擎都能看懂。',
      code: '<header>页头</header>\n<main>\n  <section>第一部分</section>\n  <section>第二部分</section>\n</main>\n<footer>页脚</footer>' },
    { cat: 'html', t: '表格 table', lang: 'html',
      d: '表格只用来放「行列对应的数据」，不要拿它拼页面布局（那是十几年以前的做法）。',
      code: '<table>\n  <tr><th>科目</th><th>分数</th></tr>\n  <tr><td>语文</td><td>92</td></tr>\n  <tr><td>数学</td><td>88</td></tr>\n</table>' },
    { cat: 'html', t: '按钮 button', lang: 'html',
      d: 'button 是能点的按钮。作品页里的按钮只是展示，真正提交由平台右下角的「提交作品」负责。',
      code: '<button class="btn">开始探索</button>' },
    { cat: 'html', t: '注释 <!-- -->', lang: 'html',
      d: '注释不会显示在页面上，用来给自己留说明。写复杂结构时很有用。',
      code: '<!-- 下面是导航栏 -->\n<nav>首页 · 作品 · 关于</nav>' },

    /* ---------------- 文字内容 ---------------- */
    { cat: 'text', t: '加粗与斜体', lang: 'html',
      d: 'strong 表示「重要」（默认加粗），em 表示「强调」（默认斜体）。',
      code: '<p>请 <strong>认真阅读</strong> 这段 <em>说明</em>。</p>' },
    { cat: 'text', t: '换行 br 与分隔线 hr', lang: 'html',
      d: 'br 是强制换行（写诗、写地址时用），hr 是一条分隔线。',
      code: '<p>第一行<br>第二行</p>\n<hr>' },
    { cat: 'text', t: '特殊符号', lang: 'html',
      d: '小于号、大于号在 HTML 里有特殊含义，要写成实体字符，否则浏览器会当成标签。',
      code: '<p>1 &lt; 2 &nbsp;&nbsp; 3 &gt; 2 &nbsp;&nbsp; © 2026</p>' },

    /* ---------------- 图片链接 ---------------- */
    { cat: 'media', t: '插入图片 img', lang: 'html',
      d: 'src 是图片地址，alt 是图片打不开时显示的文字（也是读屏软件读的内容），一定要写。',
      code: '<img src="https://picsum.photos/400/240" alt="风景示例图">' },
    { cat: 'media', t: '图片圆角与裁切', lang: 'css',
      d: 'object-fit: cover 让图片按比例填满框、不拉伸变形，配合固定宽高最好用。',
      code: 'img {\n  width: 240px;\n  height: 160px;\n  object-fit: cover;\n  border-radius: 12px;\n}' },
    { cat: 'media', t: '用 emoji 当图标', lang: 'html',
      d: '不想找图片时，emoji 就是现成的彩色图标，放大字号即可，而且截图一定不会丢。',
      code: '<div class="icons">\n  <span>🎨</span><span>📚</span><span>🚀</span>\n</div>' },

    /* ---------------- 样式基础 ---------------- */
    { cat: 'css', t: 'CSS 怎么写', lang: 'css',
      d: '格式是「选择器 { 属性: 值; }」。选择器决定「改谁」，大括号里决定「改成什么样」。',
      code: 'body {\n  background: #f6f1e7;\n  color: #333;\n}' },
    { cat: 'css', t: '三种选择器', lang: 'css',
      d: '标签选择器改一类标签；.类名 改带 class 的元素（最常用）；#id 改唯一元素。',
      code: 'p { color: #555; }        /* 所有段落 */\n.card { padding: 20px; }    /* class="card" */\n#top { height: 60px; }     /* id="top" */' },
    { cat: 'css', t: '给元素加类名', lang: 'html',
      d: '一个元素可以有多个类名，用空格隔开。先在 HTML 上写 class，再到 CSS 里描述它。',
      code: '<div class="card big">内容</div>' },
    { cat: 'css', t: '单位 px / % / rem', lang: 'css',
      d: 'px 是像素（固定），% 相对父元素宽度，rem 相对根字号。做自适应时多用 % 和 rem。',
      code: '.box {\n  width: 80%;\n  font-size: 1.1rem;\n  padding: 16px;\n}' },

    /* ---------------- 盒子与间距 ---------------- */
    { cat: 'box', t: '盒模型：内容 + 内边距 + 边框 + 外边距', lang: 'css',
      d: '每个元素都是一个盒子。padding 是「内容到边框」的距离，margin 是「盒子到盒子」的距离。',
      code: '.box {\n  padding: 20px;   /* 里面松一点 */\n  margin: 24px;    /* 外面隔开 */\n  border: 1px solid #ddd;\n}' },
    { cat: 'box', t: '边框与圆角', lang: 'css',
      d: 'border-radius 大到一半宽度就变成正圆，做头像、做药丸按钮都靠它。',
      code: '.tag {\n  border: 2px solid #b45b33;\n  border-radius: 999px;\n  padding: 6px 16px;\n}' },
    { cat: 'box', t: '宽度与最大宽度', lang: 'css',
      d: 'max-width 比 width 更稳：屏幕窄时自动缩，屏幕宽时不会拉成一条长线。',
      code: '.wrap {\n  width: 100%;\n  max-width: 900px;\n  margin: 0 auto;   /* 左右 auto = 水平居中 */\n}' },
    { cat: 'box', t: '阴影', lang: 'css',
      d: 'box-shadow 的四个值是：左右偏移、上下偏移、模糊半径、颜色。轻一点比重一点耐看。',
      code: '.card {\n  box-shadow: 0 6px 18px rgba(60, 30, 10, .10);\n}' },

    /* ---------------- 布局排版 ---------------- */
    { cat: 'layout', t: 'Flex 横向排列（最常用）', lang: 'css',
      d: '给父元素 display:flex，子元素就自动排成一行。justify-content 管水平，align-items 管垂直，gap 管间距。',
      code: '.row {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  gap: 16px;\n}' },
    { cat: 'layout', t: 'Flex 换行做卡片墙', lang: 'css',
      d: 'flex-wrap: wrap 让子元素排满一行后自动换行，配合 gap 就是整齐的卡片墙。',
      code: '.wall {\n  display: flex;\n  flex-wrap: wrap;\n  gap: 16px;\n}\n.wall .card {\n  flex: 1 1 220px;   /* 最窄 220px，空间够就变宽 */\n}' },
    { cat: 'layout', t: 'Grid 网格布局', lang: 'css',
      d: 'repeat(3, 1fr) 就是「三列等宽」。做九宫格、相册，grid 比 flex 更省事。',
      code: '.grid {\n  display: grid;\n  grid-template-columns: repeat(3, 1fr);\n  gap: 14px;\n}' },
    { cat: 'layout', t: '水平垂直都居中', lang: 'css',
      d: '给父元素三行 flex 代码，子元素就正中间。这是目前最省心的居中写法。',
      code: '.center {\n  display: flex;\n  justify-content: center;\n  align-items: center;\n  min-height: 300px;\n}' },
    { cat: 'layout', t: '左右两栏', lang: 'css',
      d: '侧栏固定宽度，主内容占剩下的空间（flex:1），窗口变窄时主内容自动收缩。',
      code: '.two-col {\n  display: flex;\n  gap: 20px;\n}\n.side { width: 200px; flex: none; }\n.main { flex: 1; }' },
    { cat: 'layout', t: '定位 position', lang: 'css',
      d: 'absolute 要配合父元素的 position: relative 使用，否则会跑到整个页面左上角。',
      code: '.card { position: relative; }\n.badge {\n  position: absolute;\n  top: 10px;\n  right: 10px;\n}' },

    /* ---------------- 颜色装饰 ---------------- */
    { cat: 'color', t: '文字色与背景色', lang: 'css',
      d: '颜色可以写 #b45b33 这种十六进制，也可以写 rgb(180, 91, 51)。浅底深字最耐看。',
      code: 'body {\n  background: #faf6ee;\n  color: #3b322a;\n}' },
    { cat: 'color', t: '渐变背景', lang: 'css',
      d: 'linear-gradient(角度, 起色, 止色)。135deg 是从左上到右下，最常见的按钮渐变。',
      code: '.hero {\n  background: linear-gradient(135deg, #f6b26b, #b45b33);\n  color: #fff;\n  padding: 48px 24px;\n}' },
    { cat: 'color', t: '半透明 rgba', lang: 'css',
      d: '最后一个数字是透明度（0 全透明，1 不透明）。做蒙层、做浅色底很好用。',
      code: '.mask {\n  background: rgba(0, 0, 0, .45);\n  color: #fff;\n  padding: 20px;\n}' },
    { cat: 'color', t: '主题色只写一处', lang: 'css',
      d: '把主色定义成变量放在 :root，后面所有地方都引用它，想换配色只改一行。',
      code: ':root { --brand: #b45b33; }\n.btn { background: var(--brand); }\n.link { color: var(--brand); }' },

    /* ---------------- 动效交互 ---------------- */
    { cat: 'fx', t: '悬停变色 :hover', lang: 'css',
      d: ':hover 表示鼠标移上去时的样子。transition 让变化有过渡，不会「啪」地跳一下。',
      code: '.btn {\n  background: #b45b33;\n  transition: background .2s;\n}\n.btn:hover {\n  background: #c9713f;\n}' },
    { cat: 'fx', t: '上浮与缩放', lang: 'css',
      d: 'transform 不改变布局，只改变「看起来的位置」，所以做悬停动效最安全。',
      code: '.card {\n  transition: transform .2s, box-shadow .2s;\n}\n.card:hover {\n  transform: translateY(-4px);\n  box-shadow: 0 12px 24px rgba(60,30,10,.16);\n}' },
    { cat: 'fx', t: '关键帧动画', lang: 'css',
      d: '@keyframes 定义动画过程，animation 把它挂到元素上。infinite 表示一直循环。',
      code: '@keyframes float {\n  0%, 100% { transform: translateY(0); }\n  50% { transform: translateY(-8px); }\n}\n.float { animation: float 2.4s ease-in-out infinite; }' },
    { cat: 'fx', t: '渐变文字', lang: 'css',
      d: '背景裁切到文字上（background-clip: text），再让文字本身透明，就得到渐变字。',
      code: '.grad {\n  background: linear-gradient(90deg, #b45b33, #5e8c61);\n  -webkit-background-clip: text;\n  background-clip: text;\n  color: transparent;\n}' },

    /* ---------------- 名词解释 ---------------- */
    { cat: 'term', t: 'HTML', lang: 'html',
      d: '超文本标记语言。用一对对「标签」告诉浏览器：这段是标题、那段是图片。它只管结构，不管好看不好看。',
      code: '<h1>这就是 HTML</h1>' },
    { cat: 'term', t: 'CSS', lang: 'css',
      d: '层叠样式表。负责颜色、大小、位置、动画。同一个 HTML 换一套 CSS，就是完全不同的样子。',
      code: 'h1 { color: #b45b33; font-size: 32px; }' },
    { cat: 'term', t: '标签 / 属性', lang: 'html',
      d: '标签是 <p> 这样的名字，属性写在标签里，形如 名字="值"，用来补充信息（比如 img 的 src）。',
      code: '<img src="图片地址" alt="说明文字" width="200">' },
    { cat: 'term', t: '选择器', lang: 'css',
      d: 'CSS 里「选谁」的那一段。写错选择器是「样式不生效」的第一大原因——先检查类名有没有拼错。',
      code: '.card-title { font-size: 18px; }' },
    { cat: 'term', t: '盒模型', lang: 'css',
      d: '浏览器把每个元素看成一个矩形盒子：内容 → padding → border → margin，由内到外四层。',
      code: '.box {\n  box-sizing: border-box;  /* 宽度包含内边距，算尺寸更省心 */\n}' },
    { cat: 'term', t: '响应式', lang: 'css',
      d: '同一份网页在手机、平板、电脑上都好看。最常用的手段是 % 宽度、flex 换行和 @media 断点。',
      code: '@media (max-width: 640px) {\n  .two-col { flex-direction: column; }\n  .side { width: auto; }\n}' }
  ]
};
