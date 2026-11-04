/* ============================================================
   打字冒险岛 · 共享内容库（typing-data.js）
   教师端 / 学生端共用：篇章定义、分级词库、课文句子、
   宠物与装扮图鉴（纯 SVG，零外部资源）。
   由 app/typing/levels.js 消费生成无限关卡。
   ============================================================ */
window.TD = (function () {
  'use strict';

  /* ---------------- 八大篇章 ----------------
     2026-11-02 扩充：新增「数字小键盘」「中英混排」，并把原「字符位置」
     拆成专门练大小写与标点的篇章。
     注意：数组顺序 = 地图上的摆放顺序，id 才是存档键（s7c1l1 这种）。
     所以 id 可以不连续 —— 小键盘要排在中文之前，编号却是 7，这是有意的：
     不动老 id，学生已有的 s5 / s6 进度一条都不会错位。 */
  var STAGES = [
    { id: 1, name: '键位熟悉', icon: '🏝', type: 'keys',  lang: 'en',
      desc: '认识键盘上的每一位伙伴：基准键 → 上排 → 下排 → 数字行 → Shift 大写' },
    { id: 2, name: '字母位置', icon: '🌊', type: 'text', lang: 'en',
      desc: '随机字母流冲浪，练习不看键盘的盲打' },
    { id: 3, name: '英文单词', icon: '🌳', type: 'text', lang: 'en',
      desc: '整词输入，从短词到长词的英文单词林' },
    { id: 4, name: '大小写与标点', icon: '🏔', type: 'text', lang: 'en',
      desc: '1-3 关首字母大写 · 4-5 关全大写 · 6-7 关标点 · 8-9 关引号冒号 · BOSS 混排' },
    { id: 7, name: '数字小键盘', icon: '🔢', type: 'text', lang: 'en', numpad: 1,
      desc: 'NumLock 打开，右手 4-5-6 定位：单行 → 全键 → 多位 → 小数算式 → 电话号码' },
    { id: 5, name: '中文词语', icon: '🏯', type: 'text', lang: 'cn',
      desc: '切换到中文输入法，用拼音打出课本词语' },
    { id: 8, name: '中英混排', icon: '🌉', type: 'text', lang: 'cn', mixed: 1,
      desc: '中文、英文、数字、标点混在一句里，练 Ctrl+空格 来回切换输入法' },
    { id: 6, name: '文章阅读', icon: '📚', type: 'text', lang: 'cn',
      desc: '七类文章任选：故事 · 写景 · 科普 · 诗歌 · 古文 · 古诗 · 课文，读到哪打到哪' }
  ];
  function stageById(id) {
    for (var i = 0; i < STAGES.length; i++) if (STAGES[i].id === Number(id)) return STAGES[i];
    return null;
  }

  /* ---------------- 篇章 1：键位课程（10 关一轮） ----------------
     每关给一组目标键；finger = 标准指法手指提示 */
  var KEYS_HOME = [
    ['f', '食指·左手'], ['j', '食指·右手'], ['d', '中指·左手'], ['k', '中指·右手'],
    ['s', '无名指·左手'], ['l', '无名指·右手'], ['a', '小指·左手'], [';', '小指·右手']
  ];
  var KEYS_TOP = [
    ['q', '小指·左手'], ['w', '无名指·左手'], ['e', '中指·左手'], ['r', '食指·左手'],
    ['t', '食指·左手'], ['y', '食指·右手'], ['u', '食指·右手'], ['i', '中指·右手'],
    ['o', '无名指·右手'], ['p', '小指·右手']
  ];
  var KEYS_BOTTOM = [
    ['z', '小指·左手'], ['x', '无名指·左手'], ['c', '中指·左手'], ['v', '食指·左手'],
    ['b', '食指·左手'], ['n', '食指·右手'], ['m', '食指·右手'], [',', '中指·右手'],
    ['.', '无名指·右手'], ['/', '小指·右手']
  ];
  /* 数字行（主键盘最上排）：1 左小指 …… 0 右小指，与 KB_FINGER_OF 完全一致 */
  var KEYS_NUMROW = [
    ['1', '小指·左手'], ['2', '无名指·左手'], ['3', '中指·左手'],
    ['4', '食指·左手'], ['5', '食指·左手'],
    ['6', '食指·右手'], ['7', '食指·右手'], ['8', '中指·右手'],
    ['9', '无名指·右手'], ['0', '小指·右手']
  ];
  /* 大写键位：目标是大写字母，必须按住对侧 Shift 才打得出来。
     hint 直接写清「按 Shift」，否则学生只会觉得"我明明按了 A 怎么错"。 */
  var KEYS_SHIFT = [
    ['A', '小指·左手 + 右Shift'], ['S', '无名指·左手 + 右Shift'],
    ['D', '中指·左手 + 右Shift'], ['F', '食指·左手 + 右Shift'],
    ['J', '食指·右手 + 左Shift'], ['K', '中指·右手 + 左Shift'],
    ['L', '无名指·右手 + 左Shift'], ['Q', '小指·左手 + 右Shift']
  ];
  var KEY_LESSONS = [KEYS_HOME, KEYS_HOME, KEYS_TOP, KEYS_TOP, KEYS_BOTTOM,
                     KEYS_BOTTOM, KEYS_HOME.concat(KEYS_TOP),
                     KEYS_TOP.concat(KEYS_BOTTOM),
                     KEYS_NUMROW.concat(KEYS_SHIFT),
                     KEYS_HOME.concat(KEYS_TOP).concat(KEYS_BOTTOM).concat(KEYS_NUMROW)];

  /* ---------------- 英文词库（按难度分带） ---------------- */
  var EN_WORDS = {
    easy: ['cat','dog','sun','run','big','red','hot','map','box','pen','cup','hat','leg','bus','egg','fox','jam','key','log','mix',
           'nap','oak','pig','ran','sit','top','van','win','yes','zip','and','bed','cut','day','eat','fly','get','hit','ice','job'],
    mid: ['apple','house','water','happy','smile','light','green','clock','bread','chair','dance','eagle','fruit','garden','heart',
          'juice','kite','lemon','money','night','ocean','party','quiet','river','school','tiger','unity','voice','watch','year',
          'zebra','book','cake','dream','earth','friends','game','hello','island','jungle'],
    hard: ['adventure','beautiful','computer','different','elephant','family','garden','hospital','important','journey',
           'knowledge','language','mountain','nature','operation','picture','question','remember','science','together',
           'umbrella','vacation','wonderful','xylophone','yesterday','keyboard','monitor','practice','student','teacher']
  };
  var EN_SENT = [
    'The sun is up.', 'I love my school.', 'Keep typing fast.', 'Good morning, class.',
    'Practice makes perfect.', 'The cat sits on the mat.', 'We learn to type today.',
    'A good book is a good friend.', 'Time and tide wait for no man.',
    'Where there is a will, there is a way.', 'Every letter has its home.',
    'My fingers fly on the keyboard.', 'Type with all ten fingers.',
    'The early bird catches the worm.', 'Little by little, one goes far.'
  ];

  /* ============================================================
     篇章 4「大小写与标点」的内容编排（2026-11-02 新增）
     ------------------------------------------------------------
     为什么要按关号分池：以前这一篇章是"数字+符号+大写"随机撒，
     学生打到第 8 卷也不知道自己到底在练哪一项。
     现在每一关对应一个明确的知识点，十关走完正好把
     「Shift 大写 → 全大写 → 英文标点 → 引号括号冒号」学一遍：

       1-2 关  首字母大写（人名校名）      Shift + 字母
       3 关    大写 + 句号结尾              Shift + .
       4-5 关  全大写词（练持续 Shift / CapsLock）
       6 关    逗号、句号                   , .
       7 关    问号、感叹号                 ? !（都需 Shift）
       8 关    引号、括号                   " ' ( )
       9 关    冒号、分号、连字符           : ; -
       10 关   混合挑战（大小写 + 全部标点 + 数字）

     每个池子都是真实可读的短句，不是无意义的符号堆叠 ——
     学生打的是"句子"，不是"字符"。 */
  var S4_MAP = {
    1: ['Monday', 'Tuesday', 'Friday', 'Sunday', 'January', 'October',
        'China', 'Beijing', 'Guangzhou', 'English', 'School', 'Teacher'],
    2: ['Tom', 'Amy', 'Lily', 'Jack', 'Peter', 'Miss Li', 'Mr Wang', 'Class One'],
    3: ['I like China.', 'My name is Tom.', 'We live in Beijing.',
        'She is from Guangzhou.', 'Today is Monday.', 'This is my school.',
        'He is my teacher.', 'I love my family.'],
    4: ['OK', 'GO', 'STOP', 'OPEN', 'CLOSE', 'ENTER', 'EXIT', 'SAVE',
        'HELLO', 'THANKS', 'WELCOME', 'FINISH'],
    5: ['USA', 'UK', 'TV', 'CD', 'PE', 'IT', 'CPU', 'USB', 'HTML', 'PDF',
        'VIP', 'GPS', 'ABC', 'DIY'],
    6: ['I am fine, thank you.', 'Sit down, please.', 'Open your book.',
        'This is a pen, not a pencil.', 'Read it again, please.',
        'Come here, Tom.', 'It is red, blue and green.'],
    7: ['Are you ready?', 'What is this?', 'How nice it is!',
        'Let us go!', 'Can you type?', 'Is it your book?',
        'What a good day!', 'Do you like it?'],
    8: ['He said, "Come in."', 'She asked, "Why?"', 'My bag (red) is here.',
        '"Good morning!" said Tom.', 'I have a book (story).',
        'He said, "I can type."'],
    9: ['Name: Tom; Age: 12.', 'Wait - it is 3:30.',
        'Well-known and up-to-date.', 'Note: keep it safe; do not lose.',
        'First: read; second: type.'],
    10: ['Hi, I am Tom. I am 12.', 'OK! Let us start, please.',
         'My school is No.1 Middle School.', 'He said: "Type 20 words!"']
  };
  /* 9 关之后每卷重排：卷号越大，从池子里拼的句子越多 */
  var S4_TOPIC = ['首字母大写', '人名地名', '大写句首', '全大写词', '全大写缩写',
                  '逗号句号', '问号感叹号', '引号括号', '冒号分号', '综合挑战'];

  /* ============================================================
     篇章 7「数字小键盘」的内容编排
     ------------------------------------------------------------
     小键盘真正的难点不是"认识数字"，而是三件事：
       ① NumLock 没开，按下去是方向键；
       ② 手指不知道该放哪 —— 右手 4/5/6 就是小键盘的"基准键"；
       ③ 不看键盘时，7-4-1 和 9-6-3 两列容易串。
     所以关号按「先单列、再全键、再多位、最后带符号」铺：

       1 关  4-5-6 基准列（手先放对位置）
       2 关  7-4-1 / 8-5-2 / 9-6-3 三列
       3 关  0-9 全键乱序
       4 关  两位数
       5 关  三位数
       6 关  小数（小数点）
       7 关  加减算式（+ -）
       8 关  乘除与等号（* / =）
       9 关  电话号码 / 日期（连字符）
       10 关 混合长串（订单号那种） */
  var S7_MAP = {
    1: ['456', '654', '456', '564', '465', '645', '444', '555', '666', '465'],
    2: ['741', '852', '963', '147', '258', '369', '789', '123', '456', '951'],
    3: ['7391', '2486', '5061', '9274', '8135', '0642', '7593', '1860', '3748', '5029'],
    4: ['12 34 56 78 90 21 43 65 87 09', '23 45 67 89 01 32 54 76 98 10'],
    5: ['135 246 357 468 579 680 791 802 913 024',
        '999 111 222 333 444 555 666 777 888 000'],
    6: ['3.14 2.5 0.5 9.99 12.8 100.5 7.25 46.3',
        '1.5 2.75 3.125 4.06 5.9 6.01 7.4 8.88'],
    7: ['12+34 56+78 90+21 45-23 67-19 88-46',
        '100+200 350-125 480+520 999-111'],
    8: ['7*8 6*9 12*3 45/5 81/9 64/8',
        '8*8=64 9*9=81 72/8=9 100/4=25'],
    9: ['0757-8632', '13800138000', '010-12345678',
        '2026-11-02', '0757-86321666', '400-800-8888'],
    10: ['2026 1102 0757 8632 6666 8888',
         '12.5 34.75 68.90 105.25 200.00',
         '3*7+12 45-8*2 96/4+15']
  };
  var S7_TOPIC = ['4-5-6 基准', '三列定位', '全键乱序', '两位数', '三位数',
                  '小数点', '加减', '乘除等号', '电话号码', '混合长串'];

  /* ============================================================
     篇章 8「中英混排」的内容编排
     ------------------------------------------------------------
     输入法切换是这个篇章唯一的目标。难点在于学生常犯的两种错：
       · 该打英文时输入法还在中文状态，敲出来的字母变成候选框；
       · 该打中文标点时用了英文标点（逗号变成 , 而不是 ，）。
     所以句子里的英文词、数字、中文标点是刻意混着放的：

       1-2 关  中文 + 一个英文词
       3-4 关  中文 + 英文词 + 数字
       5-6 关  中文标点（，。？！）占比提高
       7-8 关  引号、冒号、书名号
       9-10 关 长句、多个英文词来回切 */
  var S8_MAP = {
    1: ['我用 Word 打字。', '这是 1 台电脑。', '打开 QQ 练一练。',
        '我们学 Excel 表格。', '键盘上有很多字母。'],
    2: ['我在 3 楼上课。', '今天气温 26 度。', '这个 U 盘是 16 GB 的。',
        '请把 PPT 打开。', '电脑里有 8 个文件夹。'],
    3: ['请把 Word 文档保存。', '我用 PPT 做课件。', '浏览器里输入网址。',
        '这个软件叫 Windows。', '我用拼音打汉字。'],
    4: ['按 Ctrl 加空格换输入法。', '打字时手要放在 A S D F 上。',
        '用 Shift 可以打出大写 A。', 'NumLock 灯亮才能打数字。'],
    5: ['“请打开 Word。”老师说。', '他问：“你会用 Excel 吗？”',
        '“OK！”我大声回答。', '老师说：“打字要坐直。”'],
    6: ['我先读一遍，再打一遍，最后检查一遍。',
        '键盘分主键区、功能键区和小键盘区。',
        '打字不看键盘，速度才会越来越快。'],
    7: ['《打字入门》这本书很好看。', '老师提醒我们：“手要放回基准键。”',
        '他说：“Ctrl+空格 是中英文切换。”'],
    8: ['我用 Excel 做了一张表，里面有 30 个数字。',
        '课间我打开 Word，把这段文字打了 3 遍。',
        '键盘上的 Shift 键有两个，左右各一个。'],
    9: ['今天我们学习了 Word 和 Excel，还练了 20 分钟打字。',
        '把手指放在 A S D F 和 J K L 上，这就是基准键。'],
    10: ['老师说：“用 Ctrl+空格 切到中文，用 Shift 打大写，用小键盘打数字。”',
         '我打开 Word，先打标题，再打正文，最后按 Ctrl+S 保存。']
  };
  var S8_TOPIC = ['中文+英文词', '中文+数字', '英文软件名', '按键名混排',
                  '中文引号', '长句逗号', '书名号与冒号', '多词切换',
                  '长短句混排', '综合挑战'];

  /* ---------------- 中文词库（按年级，中小学课本常见词语） ---------------- */
  var CN_WORDS = {
    1: ['春天','夏天','秋天','冬天','太阳','月亮','星星','河水','大山','白云',
        '小鸟','青蛙','虫子','花草','树苗','同学','老师','学校','书包','铅笔',
        '尺子','橡皮','早操','写字','读书','说话','朋友','开心','快乐','家人',
        '妈妈','爸爸','爷爷','奶奶','哥哥','姐姐','身体','眼睛','耳朵','嘴巴'],
    2: ['夕阳','晚霞','田野','稻谷','果园','枫叶','雪花','北风','灯笼','深夜',
        '厨房','窗户','花园','操场','教室','黑板','粉笔','课本','故事','笑话',
        '帮助','团结','勇敢','诚实','认真','努力','进步','鼓励','希望','梦想',
        '西瓜','葡萄','香蕉','萝卜','白菜','黄瓜','南瓜','豆角','茄子','辣椒'],
    3: ['早晨','清晨','黎明','中午','傍晚','黄昏','观察','发现','思考','回答',
        '提问','练习','复习','预习','作业','考试','成绩','暑假','寒假','节日',
        '国庆','中秋','春节','端午','重阳','登高','赏月','月饼','龙舟','粽子',
        '菊花','梅花','兰花','竹子','松树','柳树','白杨','银杏','枫树','槐树'],
    4: ['奥秘','探索','宇宙','行星','地球','卫星','火箭','飞船','实验','记录',
        '数据','图表','规律','结论','猜测','验证','证据','推理','逻辑','智慧',
        '勤劳','朴素','节约','珍惜','时光','岁月','青春','童年','回忆','难忘',
        '雄伟','壮丽','辽阔','富饶','宝藏','矿藏','森林','草原','沙漠','绿洲'],
    5: ['境界','启迪','蕴含','慷慨','倾吐','殷勤','哺育','坚决','毅然','尊重',
        '怜悯','嘱咐','严峻','琢磨','恳求','沉默','敏捷','慈祥','憧憬','语塞',
        '瑰宝','精髓','辉煌','殿堂','铲除','烦躁','喧嚣','诱惑','静谧','汲取',
        '典雅','峭壁','悬崖','瀑布','山涧','幽静','蜿蜒','崎岖','挺拔','苍劲'],
    6: ['庄严','宣告','欢呼','肃立','瞻仰','飘拂','汇集','磅礴','澎湃','晶莹',
        '资源','枯竭','目睹','见证','栖息','静谧','邻居','挚友','颤抖','拒绝',
        '蔑视','隐蔽','惜别','真挚','谱写','幽谷','峭壁','悬崖','瀑布','山涧',
        '幽静','蜿蜒','崎岖','挺拔','苍劲','傲然','葱茏','婆娑','斑驳','摇曳'],
    7: ['酝酿','卖弄','嘹亮','黄晕','烘托','静默','风筝','朗润','宛转','应和',
        '憔悴','璀璨','鼎盛','慷慨','贪婪','堕落','点缀','深邃','绵延','迂回',
        '犹豫','伶仃','匀称','和谐','优雅','舒活','抖擞','呼朋引伴','花枝招展',
        '繁花嫩叶','呼风唤雨','精神抖擞','欣欣然','酝酿着','轻悄悄','软绵绵'],
    8: ['绯红','标致','落第','不逊','托辞','杳无消息','深恶痛疾','正人君子',
        '鹤立鸡群','正襟危坐','颔首低眉','诚惶诚恐','无可置疑','黯然失色',
        '广袤','迁徙','媲美','笃信','默契','冗长','褶皱','诚挚','遴选','雷同',
        '企盼','陷阱','棘手','熏陶','垣墙','油光可鉴','抑扬顿挫','翻来覆去'],
    8: ['绯红','标致','落第','不逊','托辞','抑扬顿挫','深恶痛疾','物以希为贵',
        '正人君子','杳无消息','鹤立鸡群','正襟危坐','颔首低眉','诚惶诚恐',
        '无可置疑','黯然失色','广袤','迁徙','媲美','笃信','默契','啼笑皆非',
        '冗长','褶皱','诚挚','遴选','雷同','企盼','陷阱','棘手','熏陶','垣墙'],
    9: ['妖娆','风骚','稍逊','惟余','天堑','聒噪','戏谑','烦躁','忏悔','慰藉',
        '旺盛','惬意','休憩','萦绕','氤氲','峥嵘','稠密','瑰丽','亵渎','赋予',
        '渣滓','瞑目','攫取','狼藉','红装素裹','成吉思汗','略输文采','稍逊风骚',
        '一代天骄','只识弯弓射大雕','俱往矣','数风流人物','还看今朝','疏懒']
  };

  /* ---------------- 中文句子（按学段：古诗名句 + 课文式句子） ---------------- */
  var CN_SENT = {
    low: ['床前明月光，疑是地上霜。','春眠不觉晓，处处闻啼鸟。',
          '白日依山尽，黄河入海流。','锄禾日当午，汗滴禾下土。',
          '小时不识月，呼作白玉盘。','举头望明月，低头思故乡。',
          '红豆生南国，春来发几枝。','空山不见人，但闻人语响。',
          '小荷才露尖尖角，早有蜻蜓立上头。','不知细叶谁裁出，二月春风似剪刀。'],
    mid: ['欲穷千里目，更上一层楼。','野火烧不尽，春风吹又生。',
          '遥知不是雪，为有暗香来。','飞流直下三千尺，疑是银河落九天。',
          '两岸猿声啼不住，轻舟已过万重山。','羌笛何须怨杨柳，春风不度玉门关。',
          '但愿人长久，千里共婵娟。','日出江花红胜火，春来江水绿如蓝。',
          '谁言寸草心，报得三春晖。','独在异乡为异客，每逢佳节倍思亲。',
          '停车坐爱枫林晚，霜叶红于二月花。','忽如一夜春风来，千树万树梨花开。'],
    high: ['人生自古谁无死，留取丹心照汗青。','沉舟侧畔千帆过，病树前头万木春。',
           '长风破浪会有时，直挂云帆济沧海。','会当凌绝顶，一览众山小。',
           '落红不是无情物，化作春泥更护花。','不畏浮云遮望眼，自缘身在最高层。',
           '山重水复疑无路，柳暗花明又一村。','问渠那得清如许？为有源头活水来。',
           '纸上得来终觉浅，绝知此事要躬行。','粉骨碎身浑不怕，要留清白在人间。',
           '春蚕到死丝方尽，蜡炬成灰泪始干。','采得百花成蜜后，为谁辛苦为谁甜。'],
    /* 中学段：整篇课文 —— 鲁迅《从百草园到三味书屋》（节选，部编版七下，
       约 2300 字）。按标点切成 8~27 字的短句，无限关卡就是在这篇课文里
       「走到哪打到哪」；生僻字（蝥/攒/髓/叵/拗…）正好配合点击查拼音。 */
    mid_school: ['我家的后面有一个很大的园，相传叫作百草园。',
           '现在是早已并屋子一起卖给朱文公的子孙了，',
           '连那最末次的相见也已经隔了七八年，',
           '其中似乎确凿只有一些野草；','但那时却是我的乐园。',
           '不必说碧绿的菜畦，光滑的石井栏，','高大的皂荚树，紫红的桑椹；',
           '也不必说鸣蝉在树叶里长吟，','肥胖的黄蜂伏在菜花上，',
           '轻捷的叫天子（云雀）忽然从草间直窜向云霄里去了。',
           '单是周围的短短的泥墙根一带，就有无限趣味。',
           '油蛉在这里低唱，蟋蟀们在这里弹琴。',
           '翻开断砖来，有时会遇见蜈蚣；',
           '还有斑蝥，倘若用手指按住它的脊梁，',
           '便会啪的一声，从后窍喷出一阵烟雾。',
           '何首乌藤和木莲藤缠络着，','木莲有莲房一般的果实，','何首乌有臃肿的根。',
           '有人说，何首乌根是有像人形的，','吃了便可以成仙，',
           '我于是常常拔它起来，牵连不断地拔起来，',
           '也曾因此弄坏了泥墙，','却从来没有见过有一块根像人样。',
           '如果不怕刺，还可以摘到覆盆子，','像小珊瑚珠攒成的小球，',
           '又酸又甜，色味都比桑椹要好得远。',
           '长的草里是不去的，','因为相传这园里有一条很大的赤练蛇。',
           '长妈妈曾经讲给我一个故事听：',
           '先前，有一个读书人住在古庙里用功，',
           '晚间，在院子里纳凉的时候，','突然听到有人在叫他。',
           '答应着，四面看时，',
           '却见一个美女的脸露在墙头上，向他一笑，隐去了。',
           '他很高兴；但竟给那走来夜谈的老和尚识破了机关。',
           '说他脸上有些妖气，一定遇见“美女蛇”了；',
           '这是人首蛇身的怪物，能唤人名，',
           '倘一答应，夜间便要来吃这人的肉。',
           '他自然吓得要死，而那老和尚却道无妨，',
           '给他一个小盒子，说只要放在枕边，便可高枕而卧。',
           '他虽然照样办，却总是睡不着，','当然睡不着的。',
           '到半夜，果然来了，','沙沙沙！门外像是风雨声。',
           '他正抖作一团时，却听得豁的一声，',
           '一道金光从枕边飞出，','外面便什么声音也没有了，',
           '那金光也就飞回来，敛在盒子里。',
           '后来呢？后来，老和尚说，这是飞蜈蚣，',
           '它能吸蛇的脑髓，美女蛇就被它治死了。',
           '结末的教训是：所以倘有陌生的声音叫你的名字，你万不可答应他。',
           '这故事很使我觉得做人之险，',
           '夏夜乘凉，往往有些担心，',
           '不敢去看墙上，而且极想得到一盒老和尚那样的飞蜈蚣。',
           '走到百草园的草丛旁边时，也常常这样想。',
           '但直到现在，总还没有得到，','但也没有遇见过赤练蛇和美女蛇。',
           '叫我名字的陌生声音自然是常有的，','然而都不是美女蛇。',
           '冬天的百草园比较的无味；','雪一下，可就两样了。',
           '拍雪人（将自己的全形印在雪上）和塑雪罗汉需要人们鉴赏，',
           '这是荒园，人迹罕至，所以不相宜，只好来捕鸟。',
           '薄薄的雪，是不行的；','总须积雪盖了地面一两天，',
           '鸟雀们久已无处觅食的时候才好。',
           '扫开一块雪，露出地面，','用一枝短棒支起一面大的竹筛来，',
           '下面撒些秕谷，棒上系一条长绳，',
           '人远远地牵着，看鸟雀下来啄食，',
           '走到竹筛底下的时候，将绳子一拉，便罩住了。',
           '但所得的是麻雀居多，也有白颊的“张飞鸟”，',
           '性子很躁，养不过夜的。',
           '这是闰土的父亲所传授的方法，','我却不大能用。',
           '明明见它们进去了，拉了绳，','跑去一看，却什么都没有，',
           '费了半天力，捉住的不过三四只。',
           '闰土的父亲是小半天便能捕获几十只，','装在叉袋里叫着撞着的。',
           '我曾经问他得失的缘由，','他只静静地笑道：',
           '“你太性急，来不及等它走到中间去。”',
           '我不知道为什么家里的人要将我送进书塾里去了，',
           '而且还是全城中称为最严厉的书塾。',
           '也许是因为拔何首乌毁了泥墙吧，',
           '也许是因为将砖头抛到间壁的梁家去了罢，',
           '也许是因为站在石井栏上跳了下来罢，','都无从知道。',
           '总而言之：我将不能常到百草园了。',
           'Ade，我的蟋蟀们！','Ade，我的覆盆子们和木莲们！',
           '出门向东，不上半里，走过一道石桥，','便是我的先生的家了。',
           '从一扇黑油的竹门进去，第三间是书房。',
           '中间挂着一块匾道：三味书屋；','匾下面是一幅画，',
           '画着一只很肥大的梅花鹿伏在古树下。',
           '没有孔子牌位，我们便对着那匾和鹿行礼。',
           '第一次算是拜孔子，第二次算是拜先生。',
           '第二次行礼时，先生便和蔼地在一旁答礼。',
           '他是一个高而瘦的老人，须发都花白了，','还戴着大眼镜。',
           '我对他很恭敬，因为我早听到，',
           '他是本城中极方正，质朴，博学的人。',
           '不知从哪里听来的，东方朔也很渊博，',
           '他认识一种虫，名曰“怪哉”，','冤气所化，用酒一浇，就消释了。',
           '我很想详细地知道这故事，','但阿长是不知道的，','因为她毕竟不渊博。',
           '现在得到机会了，可以问先生。',
           '“先生，‘怪哉’这虫，是怎么一回事？……”',
           '我上了生书，将要退下来的时候，赶忙问。',
           '“不知道！”','他似乎很不高兴，脸上还有怒色了。',
           '我才知道做学生是不应该问这些事的，只要读书，',
           '因为他是渊博的宿儒，决不至于不知道，',
           '所谓不知道者，乃是不愿意说。',
           '年纪比我大的人，往往如此，','我遇见过好几回了。',
           '我就只读书，正午习字，晚上对课。',
           '先生最初这几天对我很严厉，','后来却好起来了，',
           '不过给我读的书渐渐加多，','对课也渐渐地加上字去，',
           '从三言到五言，终于到七言。',
           '三味书屋后面也有一个园，',
           '虽然小，但在那里也可以爬上花坛去折蜡梅花，',
           '在地上或桂花树上寻蝉蜕。',
           '最好的工作是捉了苍蝇喂蚂蚁，','静悄悄地没有声音。',
           '然而同窗们到园里的太多，太久，可就不行了。',
           '先生在书房里便大叫起来：','“人都到哪里去了？”',
           '人们便一个一个陆续走回去；','一同回去，也不行的。',
           '他有一条戒尺，但是不常用，','也有罚跪的规则，但也不常用，',
           '普通总不过瞪几眼，大声道：','“读书！”',
           '于是大家放开喉咙读一阵书，真是人声鼎沸。',
           '有念“仁远乎哉我欲仁斯仁至矣”的，',
           '有念“笑人齿缺曰狗窦大开”的，','有念“上九潜龙勿用”的，',
           '有念“厥土下上上错厥贡苞茅橘柚”的……',
           '先生自己也念书。',
           '后来，我们的声音便低下去，静下去了，','只有他还大声朗读着：',
           '“铁如意，指挥倜傥，一坐皆惊呢；',
           '金叵罗，颠倒淋漓噫，千杯未醉嗬……”',
           '我疑心这是极好的文章，','因为读到这里，他总是微笑起来，',
           '而且将头仰起，摇着，','向后面拗过去，拗过去。',
           '先生读书入神的时候，于我们是很相宜的。',
           '有几个便用纸糊的盔甲套在指甲上做戏。',
           '我是画画儿，用一种叫作“荆川纸”的，',
           '蒙在小说的绣像上一个个描下来，','像习字时候的影写一样。',
           '读的书多起来，画的画也多起来；',
           '书没有读成，画的成绩却不少了，',
           '最成片段的是《荡寇志》和《西游记》的绣像，',
           '都有一大本。','后来，因为要钱用，卖给一个有钱的同窗了。',
           '他的父亲是开锡箔店的；','听说现在自己已经做了店主，',
           '而且快要升到绅士的地位了。','这东西早已没有了罢。']
  };

  /* ---------------- 3D 拟物宠物 · 数据（2026-11-02 重制） ----------------
     旧版是扁平色块拼贴，没有体积感。新版每只宠物只声明「主色 + 体型特征」
     这类语义参数，球面光照、高光、边缘暗角、接触阴影全部由 p3Pet() 统一
     生成 —— 八只宠物共享同一套材质语言，风格才统一。
     buddy 风格：每只靠「体型 + 专属道具」立住辨识度，不只是换个颜色。
     字段：c 主色 / eye 眼色 / ear 耳型 / tail 尾型 / 其余为可选特征开关；
     bw·bh·bcy 身体椭圆的横竖半径与中心 y，hr·hcy 头的半径与中心 y
     —— 猫是基准，其余按物种调整（柴犬更敦实、兔子瘦高、企鹅直立蛋形、
     熊猫最圆、恐龙头大身小、独角兽苗条、小龙宽肩）。 */
  var PETS = [
    { id: 'cat',     name: '小橘猫',     mood: '喜欢趴在键盘上暖手',
      c: '#F2A65A', ear: 'cat', tail: 'curl', whisker: 1, expr: 'meow', blush: 1 },
    { id: 'dog',     name: '柴犬阿黄',   mood: '打字快，摇尾巴更快',
      c: '#E8B75A', ear: 'drop', tail: 'curl', expr: 'tongue', snout: '#5B4636', brow: 1, blush: 1,
      bw: 26.5, bh: 18.6, bcy: 89.6, hr: 25, hcy: 53 },
    { id: 'rabbit',  name: '垂耳兔团团', mood: '安静型选手，稳准快',
      c: '#F3ECE2', ear: 'lop', tail: 'puff', expr: 'happy', blush: 1,
      bw: 20.6, bh: 22.4, bcy: 86.4, hr: 24, hcy: 50.6 },
    { id: 'penguin', name: '企鹅嘟嘟',   mood: '穿着燕尾服的礼仪担当',
      c: '#3B5069', ear: 'none', tail: 'none', belly: '#FFF8EC', face: '#FFF8EC',
      beak: 1, expr: 'happy', blush: 1,
      bw: 22.4, bh: 23.2, bcy: 85.6, hr: 22.6, hcy: 47.6 },
    { id: 'panda',   name: '熊猫滚滚',   mood: '慢是慢了点，但从不出错',
      c: '#FFFFFF', ear: 'round', tail: 'none', belly: '#FFFFFF', patch: '#2C2A28', expr: 'happy',
      bw: 27.4, bh: 20.2, bcy: 88.4, hr: 27.2, hcy: 54.4 },
    { id: 'dino',    name: '小恐龙跳跳', mood: '一关一关蹦，停不下来',
      c: '#63C77F', ear: 'crest', tail: 'dino', belly: '#EAF7E6', expr: 'grin', blush: 1,
      bw: 20.8, bh: 18.4, bcy: 89.2, hr: 23.2, hcy: 51.6, spikes: 1 },
    { id: 'unicorn', name: '独角兽糖糖', mood: '传说中只有三星才能见到它',
      c: '#FBF6EE', ear: 'long', tail: 'puff', mane: 1, expr: 'happy', blush: 1,
      bw: 20.2, bh: 21.2, bcy: 87.2, hr: 23.4, hcy: 50.8 },
    { id: 'dragon',  name: '小龙哒哒',   mood: '键盘上的喷火龙，终极伙伴',
      c: '#F08C52', ear: 'horn', tail: 'dino', wing: 1, belly: '#FFD9A0', expr: 'wink', blush: 1,
      bw: 24.6, bh: 19, bcy: 88.2, hr: 24.4, hcy: 52.4, tailTip: 1 },
    /* ---- 2026-11-05 新增四只（经济系统扩容）：全部复用渲染器已有的
       耳型 / 尾型 / 表情材质参数，不新增渲染分支 —— 图鉴更大，风格不散。 */
    { id: 'hamster', name: '仓鼠球球',   mood: '腮帮子里藏着给你的小礼物',
      c: '#EDC98F', ear: 'round', tail: 'puff', belly: '#FFF6DE', expr: 'happy',
      blush: 1, whisker: 1,
      bw: 25.8, bh: 21.2, bcy: 88.6, hr: 23.4, hcy: 51.8 },
    { id: 'fox',     name: '狐狸阿赤',   mood: '手速快得像一阵红风',
      c: '#E8834E', ear: 'cat', tail: 'curl', snout: '#8A4E22', brow: 1, expr: 'grin', blush: 1,
      bw: 22.6, bh: 19.6, bcy: 89.2, hr: 24.6, hcy: 52.2 },
    { id: 'frog',    name: '青蛙呱呱',   mood: '呱！这关我闭着眼也能过',
      c: '#7EC96F', ear: 'none', tail: 'none', belly: '#F2FBE0', expr: 'grin', blush: 1,
      bw: 24.2, bh: 22, bcy: 86.2, hr: 22.8, hcy: 47.8 },
    { id: 'sheep',   name: '绵羊咩咩',   mood: '云朵一样的绒毛，抱一下',
      c: '#F4EEE2', ear: 'long', tail: 'puff', belly: '#FFFFFF', expr: 'happy', blush: 1,
      bw: 26.4, bh: 21.6, bcy: 87.8, hr: 23.8, hcy: 51.2 }
  ];
  /* 玩家的伙伴默认名（QQ 表情那种"给玩偶起名"的感觉）：
     牧场大标题用这个，宠物品种名退到副标题。 */
  var PET_DEFAULT_NAME = '小豆丁';

  /* ---------------- 3D 材质工具箱 ---------------- */
  var _W = [255, 255, 255], _K = [38, 30, 26];
  function _hex(c) {
    c = String(c || '#888888').replace('#', '');
    if (c.length === 3) c = c[0] + c[0] + c[1] + c[1] + c[2] + c[2];
    var n = parseInt(c, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function _mix(a, b, t) {
    return 'rgb(' + Math.round(a[0] + (b[0] - a[0]) * t) + ',' +
      Math.round(a[1] + (b[1] - a[1]) * t) + ',' +
      Math.round(a[2] + (b[2] - a[2]) * t) + ')';
  }
  /** 由单一主色推出一整套球面光照色阶（亮部 / 本体 / 中间调 / 暗部 / 极暗） */
  function p3Pal(c) {
    var b = _hex(c);
    var lum = (b[0] * 299 + b[1] * 587 + b[2] * 114) / 1000;
    if (lum > 232) {              // 近白材质：靠暖白→灰阶造体积，否则一片死白
      return { hi: '#FFFFFF', main: _mix(b, _W, 0), mid: _mix(b, [226, 218, 207], .8),
               dark: _mix(b, [196, 186, 173], .95), deep: _mix(b, [160, 150, 138], 1) };
    }
    return { hi: _mix(b, _W, .58), main: _mix(b, _W, 0), mid: _mix(b, _K, .18),
             dark: _mix(b, _K, .40), deep: _mix(b, _K, .60) };
  }

  /* ---------------- 装扮图鉴（叠加在宠物身上，拟物材质版） ----------------
     fill 里的 {{p}} 是渐变前缀占位符：petSvg() 会替换成当前这只宠物的实例号，
     保证同页多只宠物各自取到自己的渐变，不会串色（同一 id 只能用一次）。
     2026-11-02 五次调整：
     ① 每件都补了「材质高光 + 接触投影」（投影由 petSvg 统一挂 filter），
        帽子/眼镜是搭在毛上的实体，没有影子就成了贴纸；
     ② act 是这只装扮对应的专属动作文案，act 的动画在 P3_STYLE 里，
        类名规则 .p3-svg.act-<id>，牧场里穿上就会播一遍。 */
  var DECOS = [
    { id: 'hat-red',  name: '小红帽',   need: 10, cat: 'head', price: 150, act: '抬手敬了个礼', box: '31 2 58 40',
      svg: /* 帽身是一条完整的弧顶，不再用「双弧线」画 —— 上一版内外弧几乎平行，
             渲染出来是一圈细带，看着像戴了个发箍。 */
           '<path d="M36 34c0-15 10-23 24-23s24 8 24 23Z" fill="url(#{{p}}red)"/>' +
           '<path d="M44 28c3-9 9-13 16-13" stroke="#FFC9B8" stroke-opacity=".45" stroke-width="3.4" fill="none" stroke-linecap="round"/>' +
           '<path d="M36 34h48v4.5H36Z" fill="#9C2A1B" fill-opacity=".32"/>' +
           '<rect x="33" y="31" width="54" height="8.6" rx="4.3" fill="url(#{{p}}redDark)"/>' +
           '<ellipse cx="42" cy="33.6" rx="16" ry="2" fill="#fff" fill-opacity=".22"/>' +
           '<circle cx="59" cy="10" r="6.2" fill="url(#{{p}}cream)"/>' +
           '<ellipse cx="57" cy="8" rx="2" ry="1.6" fill="#fff" fill-opacity=".85"/>' },
    { id: 'crown',    name: '黄金皇冠', need: 20, cat: 'head', price: 300, act: '挺起胸膛，威风了一下', box: '38 14 44 30',
      svg: '<path d="M42 37 46 19l8 10 6-14 6 14 8-10 4 18Z" fill="url(#{{p}}gold)" stroke="#B8891F" stroke-width="1.2" stroke-linejoin="round"/>' +
           '<path d="M45 33.5 48 22l6.5 8.5Z" fill="#fff" fill-opacity=".42"/>' +
           '<path d="M64 20.5l5 9.5-6 .6Z" fill="#FFF8DC" fill-opacity=".55"/>' +
           '<ellipse cx="60" cy="36.5" rx="18" ry="2.4" fill="#8E6A16" fill-opacity=".35"/>' +
           '<circle cx="60" cy="23" r="2.8" fill="url(#{{p}}gemR)"/>' +
           '<circle cx="59" cy="22" r=".9" fill="#fff" fill-opacity=".85"/>' +
           '<circle cx="49.5" cy="27.5" r="2.1" fill="url(#{{p}}gemB)"/>' +
           '<circle cx="70.5" cy="27.5" r="2.1" fill="url(#{{p}}gemG)"/>' },
    { id: 'sunglass', name: '炫酷墨镜', need: 40, cat: 'face', price: 400, act: '酷酷地甩了下头', box: '29 43 60 24',
      svg: /* 整体下移 3px：眼睛中心在 y=55.5、竖半径 6.1（下沿 61.6），
             原来的镜片只盖到 59，瞳孔下半截从镜片底下露出来。 */
           '<g transform="translate(0,3)">' +
           '<rect x="40" y="46.5" width="17.5" height="12.5" rx="5.2" fill="url(#{{p}}lens)"/>' +
           '<rect x="62.5" y="46.5" width="17.5" height="12.5" rx="5.2" fill="url(#{{p}}lens)"/>' +
           '<path d="M57.5 50.5h5M40 49l-7.5-3M80 49l7.5-3" stroke="#2A3440" stroke-width="2.6" stroke-linecap="round"/>' +
           '<path d="M43 50.5l6.5 5.5" stroke="#A9C6DD" stroke-width="2.4" stroke-linecap="round" opacity=".85"/>' +
           '<path d="M65.5 50.5l6.5 5.5" stroke="#A9C6DD" stroke-width="2.4" stroke-linecap="round" opacity=".85"/>' +
           '<path d="M45 58l10-10.5" stroke="#fff" stroke-opacity=".22" stroke-width="2.6" stroke-linecap="round"/>' +
           '<path d="M67.5 58l10-10.5" stroke="#fff" stroke-opacity=".22" stroke-width="2.6" stroke-linecap="round"/>' +
           '</g>' },
    { id: 'glasses',  name: '圆眼镜',   need: 60, cat: 'face', price: 380, act: '把眼镜往上推了推', box: '31 42 58 26',
      svg: '<circle cx="50" cy="54" r="8.2" fill="#DFF1FF" fill-opacity=".28"/>' +
           '<circle cx="70" cy="54" r="8.2" fill="#DFF1FF" fill-opacity=".28"/>' +
           '<circle cx="50" cy="54" r="8.2" fill="none" stroke="url(#{{p}}wood)" stroke-width="2.8"/>' +
           '<circle cx="70" cy="54" r="8.2" fill="none" stroke="url(#{{p}}wood)" stroke-width="2.8"/>' +
           '<path d="M58.2 54h4M41.8 52.2l-8-2M78.2 52.2l8-2" stroke="#7A5230" stroke-width="2.5" stroke-linecap="round"/>' +
           '<path d="M45.5 49.5q2-2.4 4.6-1.6" stroke="#fff" stroke-opacity=".8" stroke-width="1.8" fill="none" stroke-linecap="round"/>' +
           '<path d="M65.5 49.5q2-2.4 4.6-1.6" stroke="#fff" stroke-opacity=".7" stroke-width="1.8" fill="none" stroke-linecap="round"/>' +
           '<path d="M45 58.2a8.2 8.2 0 0 0 10 .4" stroke="#5C3A20" stroke-opacity=".3" stroke-width="1.4" fill="none"/>' },
    { id: 'scarf',    name: '红围巾',   need: 80, cat: 'top', price: 500, act: '裹紧围巾打了个哆嗦', box: '36 66 52 38',
      svg: /* 围巾必须贴着「脖子」而不是「身体」：头部是 r26 圆心 (60,53)，
             y=71 处头轮廓正好在 42~78 —— 所以带子上沿取 42→78，
             下沿再外扩 10px 落到身体上（y=81 时身体横跨 38~82），两端才不会悬空。
             上一版从 34 画到 86，比脖子宽了一圈，两侧翘在空气里像翅膀。 */
           '<path d="M42 71q18 13 36 0l2.4 10.4q-20.4 12.6-40.8 0Z" fill="url(#{{p}}red)"/>' +
           '<path d="M42 71q18 13 36 0l1 4.2q-19 11.6-38 0Z" fill="#9C2A1B" fill-opacity=".28"/>' +
           '<path d="M46 74.5q14 10 28 0" stroke="#FFC9B8" stroke-opacity=".5" stroke-width="2.4" fill="none" stroke-linecap="round"/>' +
           '<path d="M48 79.5v7.5M54 81.5v7.5M60 82.4v7.5M66 81.5v7.5M72 79.5v7.5" stroke="#8E2A1D" stroke-opacity=".24" stroke-width="1.5" stroke-linecap="round"/>' +
           '<path d="M76 81l9.6 16.4-9 4.2-7.6-15.6Z" fill="url(#{{p}}redDark)"/>' +
           '<path d="M78.4 85l6 11" stroke="#FFC9B8" stroke-opacity=".34" stroke-width="2" stroke-linecap="round"/>' },
    { id: 'bow',      name: '蝴蝶结',   need: 100, cat: 'head', price: 650, act: '开心地转了个圈', box: '36 14 48 30',
      svg: /* 两片水滴形缎面 + 中间结，缎面的高光沿弧线走，才有布的厚度。
             整体下移 5px：原来结心在 y=24，比头顶(27)还高，看着像浮在头上。 */
           '<g transform="translate(0,5)">' +
           '<path d="M57 24c-5-9-13-12-18-7-5 5-3 13 3 16 4 2 10 1 15-6Z" fill="url(#{{p}}pink)"/>' +
           '<path d="M63 24c5-9 13-12 18-7 5 5 3 13-3 16-4 2-10 1-15-6Z" fill="url(#{{p}}pink)"/>' +
           '<path d="M56 26.5c-2.4-3-6.4-5-11.4-4.4" stroke="#C8558A" stroke-opacity=".28" stroke-width="1.6" fill="none" stroke-linecap="round"/>' +
           '<path d="M64 26.5c2.4-3 6.4-5 11.4-4.4" stroke="#C8558A" stroke-opacity=".24" stroke-width="1.6" fill="none" stroke-linecap="round"/>' +
           '<path d="M45 19.5c-3-2.6-6.6-2.6-8.6-.4" stroke="#fff" stroke-opacity=".5" stroke-width="2.2" fill="none" stroke-linecap="round"/>' +
           '<path d="M75 19.5c3-2.6 6.6-2.6 8.6-.4" stroke="#fff" stroke-opacity=".42" stroke-width="2.2" fill="none" stroke-linecap="round"/>' +
           '<circle cx="60" cy="24" r="6.2" fill="url(#{{p}}pinkDeep)"/>' +
           '<ellipse cx="58" cy="22" rx="2" ry="1.5" fill="#fff" fill-opacity=".82"/>' +
           '</g>' },
    { id: 'headset',  name: '星耳机',   need: 130, cat: 'head', price: 750, act: '跟着音乐摇了起来', box: '24 17 66 52',
      svg: /* 头梁半径要等于头半径（r26）：上一版 q 到 -28 且左右各外扩到 34/86，
             头梁整个悬在头顶上方，像顶了个蓝色光环。现在头梁顶点 y=26（头顶点 27），
             耳罩跨在头轮廓上（左 28.4~41.6，右 78.4~91.6），才像真戴上。 */
           '<path d="M36 52q0-26 24-26t24 26" fill="none" stroke="url(#{{p}}blueDark)" stroke-width="7.4" stroke-linecap="round"/>' +
           '<path d="M37.8 50q0-22.8 22.2-22.8t22.2 22.8" fill="none" stroke="url(#{{p}}blue)" stroke-width="4.2" stroke-linecap="round"/>' +
           '<path d="M42 41q3.6-14.4 16.8-14.4" stroke="#fff" stroke-opacity=".55" stroke-width="1.9" fill="none" stroke-linecap="round"/>' +
           '<rect x="28.4" y="44.5" width="13.2" height="20.4" rx="6.4" fill="url(#{{p}}blueDark)"/>' +
           '<rect x="78.4" y="44.5" width="13.2" height="20.4" rx="6.4" fill="url(#{{p}}blueDark)"/>' +
           '<rect x="30.4" y="46.6" width="9.2" height="16.2" rx="4.6" fill="url(#{{p}}blue)" opacity=".55"/>' +
           '<rect x="80.4" y="46.6" width="9.2" height="16.2" rx="4.6" fill="url(#{{p}}blue)" opacity=".55"/>' +
           '<ellipse cx="32" cy="49.4" rx="3" ry="3.8" fill="#fff" fill-opacity=".34"/>' +
           '<ellipse cx="82" cy="49.4" rx="3" ry="3.8" fill="#fff" fill-opacity=".26"/>' +
           '<circle cx="35" cy="57" r=".9" fill="#BFE0FF" opacity=".7"/>' +
           '<circle cx="35" cy="60.4" r=".9" fill="#BFE0FF" opacity=".5"/>' +
           '<circle cx="85" cy="57" r=".9" fill="#BFE0FF" opacity=".7"/>' +
           '<circle cx="85" cy="60.4" r=".9" fill="#BFE0FF" opacity=".5"/>' +
           '<path d="M96 33l2.5 5.4 5.4 1.1-4.1 4 1 5.5-4.8-2.7" fill="url(#{{p}}gold)"/>' },
    { id: 'bg-star',  name: '星光舞台',  need: 160, cat: 'suit', price: 1000, act: '开心地蹦了两下', box: '4 4 112 112',
      svg: '<circle cx="60" cy="62" r="53" fill="url(#{{p}}halo)"/>' +
           '<path d="M28 33l2.6 5.8 5.8 2.6-5.8 2.6-2.6 5.8-2.6-5.8-5.8-2.6 5.8-2.6Z" fill="url(#{{p}}gold)"/>' +
           '<path d="M97 43l1.9 4.2 4.2 1.9-4.2 1.9-1.9 4.2-1.9-4.2-4.2-1.9 4.2-1.9Z" fill="url(#{{p}}pink)"/>' +
           '<path d="M88 93l1.6 3.6 3.6 1.6-3.6 1.6-1.6 3.6-1.6-3.6-3.6-1.6 3.6-1.6Z" fill="url(#{{p}}blue)"/>' +
           '<path d="M22 78l1.4 3.1 3.1 1.4-3.1 1.4-1.4 3.1-1.4-3.1-3.1-1.4 3.1-1.4Z" fill="url(#{{p}}cream)"/>' },

    /* ---- 2026-11-02 新增：补齐「上衣 / 下衣 / 整套」三个部位 ----
       之前八件全是头饰和眼镜，学生换装等于只能换帽子；现在按部位分了
       五类，每个部位都有可选的衣服，才谈得上「搭配」。 */
    { id: 'vest',     name: '小马甲',   need: 0, cat: 'top', price: 450,
      act: '低头整理了一下衣领', box: '30 70 60 40',
      svg: /* 两片前襟 + 后领，中间故意敞开露出肚子 —— 马甲穿在圆身上，
             全包起来会变成一件"背心袋"，反而看不出是衣服。 */
           '<path d="M41 73q-9 4-9 16v13q0 4 4 4h11V76Z" fill="url(#{{p}}blue)"/>' +
           '<path d="M79 73q9 4 9 16v13q0 4-4 4H73V76Z" fill="url(#{{p}}blue)"/>' +
           '<path d="M41 73q9 6 19 6t19-6l-1.6-4.2q-8.4 5.2-17.4 5.2t-17.4-5.2Z" fill="url(#{{p}}blueDark)"/>' +
           '<path d="M38 84q-2 3-2 7v10" stroke="#fff" stroke-opacity=".42" stroke-width="2" fill="none" stroke-linecap="round"/>' +
           '<path d="M50 82v20M70 82v20" stroke="#3C5B8A" stroke-opacity=".35" stroke-width="1.4" stroke-linecap="round"/>' +
           '<circle cx="60" cy="87" r="1.7" fill="url(#{{p}}gold)"/>' +
           '<circle cx="60" cy="95" r="1.7" fill="url(#{{p}}gold)"/>' },
    { id: 'overall',  name: '背带裤',   need: 0, cat: 'bottom', price: 550,
      act: '得意地拍了拍裤兜', box: '34 72 52 42',
      svg: /* 裤体压在身体下半 + 两条背带跨过肩膀：背带是"背带裤"三个字
             唯一的辨识点，少了它就和一条蓝围裙没区别。 */
           '<path d="M46 78l-1.4 16M74 78l1.4 16" stroke="url(#{{p}}blueDark)" stroke-width="4.6" stroke-linecap="round"/>' +
           '<path d="M37.5 91h45v12.4q0 5.2-5.2 5.2H42.7q-5.2 0-5.2-5.2Z" fill="url(#{{p}}blue)"/>' +
           '<path d="M37.5 91h45v4.2h-45Z" fill="#2E4A72" fill-opacity=".38"/>' +
           '<rect x="52" y="98.5" width="16" height="9.6" rx="2.4" fill="#3A5B8E" fill-opacity=".55"/>' +
           '<path d="M52 101.6h16" stroke="#fff" stroke-opacity=".22" stroke-width="1.2"/>' +
           '<circle cx="46" cy="93.4" r="1.7" fill="url(#{{p}}gold)"/>' +
           '<circle cx="74" cy="93.4" r="1.7" fill="url(#{{p}}gold)"/>' +
           '<path d="M41 96v10" stroke="#8FB6E8" stroke-opacity=".45" stroke-width="1.6" stroke-linecap="round"/>' },
    { id: 'skirt',    name: '百褶裙',   need: 0, cat: 'bottom', price: 550,
      act: '转了半圈，裙摆飘起来', box: '30 85 60 32',
      svg: /* 腰头 + 展开的裙摆 + 五道褶线：褶线是百褶裙的命，画成
             一整片就是围裙。 */
           '<rect x="42" y="89" width="36" height="5.6" rx="2.6" fill="url(#{{p}}pinkDeep)"/>' +
           '<path d="M42 94h36l6.6 14.4q-24.6 7.4-49.2 0Z" fill="url(#{{p}}pink)"/>' +
           '<path d="M48 95.5l-2.6 13.6M54 95.5l-1.2 14.4M60 95.5v14.8M66 95.5l1.2 14.4M72 95.5l2.6 13.6" stroke="#C8558A" stroke-opacity=".28" stroke-width="1.5" stroke-linecap="round"/>' +
           '<path d="M44.5 96.6l-3.4 9.6" stroke="#fff" stroke-opacity=".5" stroke-width="2" stroke-linecap="round"/>' },
    { id: 'robe',     name: '博士袍',   need: 0, cat: 'suit', price: 800,
      act: '清了清嗓子，装成小博士', box: '26 66 68 46',
      svg: /* 整套服：一件盖住上半身和下半身的长袍，配 V 领和金色前襟。
             与上衣/下衣互斥 —— 穿整套时不用再挑上下装。 */
           '<path d="M43 71q-11 5-11 19v15h56V90q0-14-11-19q-9 5-17 5t-17-5Z" fill="url(#{{p}}blueDark)"/>' +
           '<path d="M49 71.5l11 14 11-14q-5 3.4-11 3.4t-11-3.4Z" fill="#1F3556"/>' +
           '<path d="M60 86v19" stroke="url(#{{p}}gold)" stroke-width="2.4" stroke-linecap="round"/>' +
           '<path d="M49 71.5l11 14M71 71.5l-11 14" stroke="url(#{{p}}gold)" stroke-width="1.8" stroke-opacity=".85" stroke-linecap="round"/>' +
           '<path d="M36.5 95h47" stroke="#16263F" stroke-opacity=".45" stroke-width="3"/>' +
           '<rect x="55" y="92.6" width="10" height="5" rx="1.6" fill="url(#{{p}}gold)" opacity=".9"/>' +
           '<path d="M35 84q-2.6 5-2.6 10" stroke="#8FB6E8" stroke-opacity=".38" stroke-width="2.2" fill="none" stroke-linecap="round"/>' +
           '<path d="M45 100.5v9M75 100.5v9" stroke="#16263F" stroke-opacity=".3" stroke-width="2" stroke-linecap="round"/>' },

    /* ---- 2026-11-05 新增三件（经济系统扩容）：补齐头饰 / 面饰 / 领口部位的
       可选项。价格已随全店涨价同步抬高（与 typing_store.py DECO_PRICE 一致）。 */
    { id: 'cap',      name: '棒球帽',   need: 0, cat: 'head', price: 320,
      act: '压了压帽檐，蓄力待发', box: '28 6 76 40',
      svg: '<path d="M36 35c0-15 10-24 24-24s24 9 24 24Z" fill="url(#{{p}}blue)"/>' +
           '<path d="M44 28c3-9 9-13 16-13" stroke="#fff" stroke-opacity=".5" stroke-width="3.2" fill="none" stroke-linecap="round"/>' +
           '<rect x="33" y="32" width="54" height="7" rx="3.5" fill="url(#{{p}}blueDark)"/>' +
           '<path d="M84 35q13 1 15 6-9 4-17 2Z" fill="url(#{{p}}blueDark)"/>' +
           '<circle cx="60" cy="14" r="3.6" fill="url(#{{p}}cream)"/>' +
           '<ellipse cx="44" cy="34" rx="12" ry="1.8" fill="#fff" fill-opacity=".24"/>' },
    { id: 'earmuffs', name: '雪绒耳罩', need: 0, cat: 'face', price: 260,
      act: '耳朵暖暖的，心里也暖暖的', box: '18 22 84 40',
      svg: '<path d="M34 52q0-25 26-25t26 25" fill="none" stroke="url(#{{p}}blueDark)" stroke-width="5" stroke-linecap="round"/>' +
           '<path d="M36 49q2-20 24-20" stroke="#fff" stroke-opacity=".5" stroke-width="1.8" fill="none" stroke-linecap="round"/>' +
           '<circle cx="30" cy="54" r="9.6" fill="url(#{{p}}cream)"/>' +
           '<circle cx="90" cy="54" r="9.6" fill="url(#{{p}}cream)"/>' +
           '<circle cx="27" cy="50.5" r="3" fill="#fff" fill-opacity=".6"/>' +
           '<circle cx="87" cy="50.5" r="3" fill="#fff" fill-opacity=".6"/>' +
           '<circle cx="33" cy="58" r="1.4" fill="#D9C8A6" opacity=".7"/>' +
           '<circle cx="93" cy="58" r="1.4" fill="#D9C8A6" opacity=".7"/>' },
    { id: 'bowtie',   name: '小红领结', need: 0, cat: 'top', price: 280,
      act: '精神抖擞地立正站好', box: '40 64 40 24',
      svg: '<path d="M60 76c-7-6-16-7-20-3-3 3-1 9 5 11 5 2 11 0 15-4Z" fill="url(#{{p}}red)"/>' +
           '<path d="M60 76c7-6 16-7 20-3 3 3 1 9-5 11-5 2-11 0-15-4Z" fill="url(#{{p}}red)"/>' +
           '<rect x="55.5" y="71.5" width="9" height="9" rx="3" fill="url(#{{p}}redDark)"/>' +
           '<ellipse cx="57.5" cy="73.5" rx="2" ry="1.4" fill="#fff" fill-opacity=".6"/>' }
  ];

  /* ---------------- 装扮部位（同类互斥） ----------------
     为什么要有部位：原来八件里七件是头饰/眼镜，学生"换装"其实只是换帽子，
     而且可以同时戴皇冠 + 蝴蝶结 + 耳机，顶上叠三层，既不像话也不好看。
     现在按真实穿衣逻辑分五个槽位，一个槽位只能有一件。 */
  var DECO_CATS = [
    { id: 'head',   name: '头饰', icon: '🎩' },
    { id: 'face',   name: '面饰', icon: '🕶' },
    { id: 'top',    name: '上衣', icon: '👕' },
    { id: 'bottom', name: '下衣', icon: '👖' },
    { id: 'suit',   name: '整套', icon: '👗' }
  ];
  function catName(cid) {
    for (var i = 0; i < DECO_CATS.length; i++) if (DECO_CATS[i].id === cid) return DECO_CATS[i].name;
    return '装饰';
  }
  /** 两件装扮是否抢同一个槽位。
      整套是特例：它同时占上衣和下衣，所以与这两类都互斥。 */
  function slotClash(a, b) {
    if (!a || !b) return false;
    if (a.cat === b.cat) return true;
    if (a.cat === 'suit' && (b.cat === 'top' || b.cat === 'bottom')) return true;
    if (b.cat === 'suit' && (a.cat === 'top' || a.cat === 'bottom')) return true;
    return false;
  }
  /** 返回穿上/脱下 id 之后的新数组（纯函数，不改原数组）。
      已穿着 → 脱下；没穿着 → 穿上，并自动摘掉同槽位的那件。 */
  function equipDeco(list, id) {
    var d = decoById(id);
    var out = (list || []).slice();
    if (!d) return out;
    var i = out.indexOf(id);
    if (i >= 0) { out.splice(i, 1); return out; }
    out = out.filter(function (x) { return !slotClash(decoById(x), d); });
    out.push(id);
    return out;
  }

  /* ---------------- 道具（金币购买，辅助通关） ----------------
     设计红线：道具只能「降低卡关的挫败」，不能「替代练习」。
     所以四个道具全部只作用于「提示 / 限时 / 通关线 / 金币」，
     没有一件能改星级速度线 —— 想拿 2 星 3 星，手速还是得自己练出来。 */
  var ITEMS = [
    { id: 'hint',   name: '指法提示卡', icon: '💡', price: 30, max: 3,
      desc: '本关键盘与指法图常亮提示，找不到键时不用低头',
      tip: '不改星级，只是把提示留在屏幕上' },
    { id: 'extra',  name: '加时卡',     icon: '⏳', price: 45, max: 3,
      desc: '本关限时延长一半，慢慢打也来得及',
      tip: '限时只防卡死，速度线不变' },
    { id: 'shield', name: '容错卡',     icon: '🛡', price: 80, max: 2,
      desc: '本关准确率 87% 也算通关，先过关再说',
      tip: '只放宽一星通关线，2 星 3 星标准一点没降' },
    { id: 'double', name: '加倍卡',     icon: '✨', price: 60, max: 2,
      desc: '本关金币翻倍，多练一会儿更划算',
      tip: '不影响星级与速度线' }
  ];
  function itemById(id) { for (var i = 0; i < ITEMS.length; i++) if (ITEMS[i].id === id) return ITEMS[i]; return null; }

  /* ---------------- 牧场背景（金币购买，2026-11-05） ----------------
     css 会整段写进牧场舞台 .stage-box 的 background —— 纯 CSS 渐变，
     零外部资源。默认背景 '' 不收钱，人人都有；
     其余按价格从草原到宇宙排，越贵越「远」。 */
  var BACKGROUNDS = [
    { id: '',        name: '默认暖光台', price: 0,
      css: 'radial-gradient(118% 92% at 50% 6%,#FFFAF1 0%,#FBE9D7 46%,#F1D9C4 100%)' },
    { id: 'meadow',  name: '青青草原', price: 200,
      css: 'linear-gradient(180deg, #BFE6FF 0%, #D8F2D0 55%, #A8DB8E 100%)' },
    { id: 'beach',   name: '阳光沙滩', price: 350,
      css: 'linear-gradient(180deg, #8FD4FF 0%, #CBEFFF 45%, #FDEBB0 72%, #F0CE7E 100%)' },
    { id: 'night',   name: '萤火星夜', price: 500,
      css: 'radial-gradient(90% 70% at 50% 100%, #274A6D 0%, #16304F 55%, #0C1D33 100%)' },
    { id: 'candy',   name: '糖果城堡', price: 800,
      css: 'linear-gradient(160deg, #FFD9EC 0%, #E7C6FF 45%, #C3AEFF 100%)' },
    { id: 'space',   name: '太空漫游', price: 1200,
      css: 'radial-gradient(80% 60% at 30% 20%, #2A3A66 0%, #131C36 55%, #070B18 100%)' }
  ];
  function bgById(id) {
    for (var i = 0; i < BACKGROUNDS.length; i++) if (BACKGROUNDS[i].id === (id || '')) return BACKGROUNDS[i];
    return BACKGROUNDS[0];
  }

  /* ---------------- 出战特效（积分购买，2026-11-05） ----------------
     参考 QQ 头像动态挂件：粒子 / 光环 / 全屏氛围三类，全部 CSS 动画实现
     （学生.html 里 .stage-fx.fx-<id> 定义具体动画），零图片零外部资源。
     积分靠打出好成绩攒（关卡越难积分越高），特效是全店最贵的奢侈品，
     排面天花板。 */
  var EFFECTS = [
    { id: 'sparkle', name: '星光闪烁',  price: 200, icon: '✨',
      desc: '伙伴身边星星一闪一闪' },
    { id: 'bubbles', name: '梦幻泡泡',  price: 400, icon: '🫧',
      desc: '泡泡从伙伴身边缓缓升起' },
    { id: 'hearts',  name: '爱心环绕',  price: 650, icon: '💖',
      desc: '粉红爱心围着伙伴转圈圈' },
    { id: 'snow',    name: '漫天雪花',  price: 900, icon: '❄️',
      desc: '细雪落在牧场舞台上' },
    { id: 'rainbow', name: '彩虹光环',  price: 1400, icon: '🌈',
      desc: '头顶一道旋转的彩虹光环' },
    { id: 'aurora',  name: '极光帷幕',  price: 2000, icon: '🌌',
      desc: '整个舞台流动着绿紫色极光' }
  ];
  function effectById(id) { for (var i = 0; i < EFFECTS.length; i++) if (EFFECTS[i].id === id) return EFFECTS[i]; return null; }

  /* ---------------- 称号（总星数升级） ---------------- */
  var TITLES = [
    { need: 0,   name: '键盘新兵' },
    { need: 10,  name: '小键盘侠' },
    { need: 30,  name: '键盘骑士' },
    { need: 60,  name: '圣键武士' },
    { need: 100, name: '键盘大师' },
    { need: 150, name: '键盘传说' },
    { need: 220, name: '键盘之神' }
  ];

  /* ---------------- 星级与速度线参数 ---------------- */
  var STAR_RULE = {
    passAcc: 90,          // 1 星：准确率下限
    perfectAcc: 97,       // 3 星额外要求
    /* 每章基础速度线（字符/分）。新增两章的理由：
       小键盘是纯数字、指法固定，比字母流快，给 30；
       中英混排每换一次输入法都要停一下，比纯中文还慢，给 15。 */
    speedBase: { 1: 26, 2: 34, 3: 26, 4: 22, 5: 16, 6: 12, 7: 30, 8: 15 },
    speedGrow: 0.05       // 每卷递增 5%
  };

  /* 一~九年级选项（教师端用） */
  var GRADES = ['一年级','二年级','三年级','四年级','五年级','六年级','七年级','八年级','九年级'];
  var GRADE_GROUP = { 1:'low',2:'low',3:'mid',4:'mid',5:'mid',6:'high',7:'mid_school',8:'mid_school',9:'mid_school' };

  function petById(id) { for (var i = 0; i < PETS.length; i++) if (PETS[i].id === id) return PETS[i]; return null; }
  function decoById(id) { for (var i = 0; i < DECOS.length; i++) if (DECOS[i].id === id) return DECOS[i]; return null; }
  function titleFor(stars) {
    var t = TITLES[0];
    for (var i = 0; i < TITLES.length; i++) if (stars >= TITLES[i].need) t = TITLES[i];
    return t;
  }
  function nextTitle(stars) {
    for (var i = 0; i < TITLES.length; i++) if (stars < TITLES[i].need) return TITLES[i];
    return null;
  }

  /* ================= 3D 拟物宠物 · 渲染器 =================
     设计要点：
     ① 体积：头部/躯干都用「径向渐变 + 左上高光 + 底部环境反光 + 边缘暗角」
        四层叠加，模拟一个受光的球体，而不是一块平涂色。
     ② 落地：宠物脚下有一枚柔和的径向阴影，否则会"飘"在台上。
     ③ 呼吸：整体以脚底为支点做 1.026 倍缩放，3.6s 一个来回 — 最快看出"活着"。
     ④ 微动作：头部轻摆、眨眼、耳朵/尾巴轻晃，各自周期不同且带随机相位，
        避免整齐划一像机器。
     ⑤ 每个实例的渐变 id 加流水号前缀，同页八只宠物各取各的，不会串色。 */
  var _p3seq = 0;

  /* 动画用 CSS 内嵌在 SVG 里：这样无论是学生端、教师端排行榜，
     还是将来 <img> 或另存为 .svg 单独打开，动效都能跟着走。 */
  var P3_STYLE = '<style>' +
    '.p3-breathe{transform-origin:0 0;animation:p3Breathe 3.6s ease-in-out infinite}' +
    '.p3-head{transform-origin:0 0;animation:p3Nodd 5.2s ease-in-out infinite}' +
    '.p3-eye{transform-origin:0 0;animation:p3Blink 5.6s ease-in-out infinite}' +
    '.p3-ear{transform-origin:0 0;animation:p3Ear 3.2s ease-in-out infinite}' +
    '.p3-tail{transform-origin:0 0;animation:p3Tail 2.7s ease-in-out infinite}' +
    '@keyframes p3Breathe{0%,100%{transform:scale(1,1)}50%{transform:scale(1.026,1.018)}}' +
    '@keyframes p3Nodd{0%,100%{transform:rotate(-1.1deg)}50%{transform:rotate(1.1deg)}}' +
    '@keyframes p3Blink{0%,92%,100%{transform:scaleY(1)}94.2%{transform:scaleY(.08)}96.4%{transform:scaleY(1)}}' +
    '@keyframes p3Ear{0%,100%{transform:rotate(-2.2deg)}50%{transform:rotate(2.2deg)}}' +
    '@keyframes p3Tail{0%,100%{transform:rotate(-3deg)}50%{transform:rotate(3deg)}}' +
    /* 装扮专属动作：牧场里穿上哪件，宠物就做哪个动作。
       动画挂在 <svg> 根节点上（走 CSS transform），一次性播放，重渲染即重播。 */
    '.p3-svg{transform-origin:50% 62%}' +
    '.p3-svg.act-hat-red{animation:p3ActTip 1.15s cubic-bezier(.36,.07,.19,.97) both}' +
    '@keyframes p3ActTip{0%,100%{transform:none}18%{transform:rotate(-6deg) translateY(-3px)}' +
      '44%{transform:rotate(3deg)}70%{transform:rotate(-2deg) translateY(-1px)}}' +
    '.p3-svg.act-crown{animation:p3ActProud 1.1s ease-in-out both}' +
    '@keyframes p3ActProud{0%,100%{transform:none}35%{transform:translateY(-4px) scale(1.045,1.06)}' +
      '70%{transform:translateY(-2px) scale(1.02,1.03)}}' +
    '.p3-svg.act-sunglass{animation:p3ActCool 1s ease-in-out both}' +
    '@keyframes p3ActCool{0%,100%{transform:none}22%{transform:rotate(-8deg)}' +
      '46%{transform:rotate(7deg)}70%{transform:rotate(-3deg)}}' +
    '.p3-svg.act-glasses{animation:p3ActPush 1.05s ease-in-out both}' +
    '@keyframes p3ActPush{0%,100%{transform:none}30%{transform:translateY(3px) scale(.985)}' +
      '58%{transform:translateY(-3px) scale(1.03)}}' +
    '.p3-svg.act-scarf{animation:p3ActShiver .9s linear both}' +
    '@keyframes p3ActShiver{0%,100%{transform:none}10%{transform:translateX(-3px)}' +
      '20%{transform:translateX(3px)}30%{transform:translateX(-3px)}40%{transform:translateX(3px)}' +
      '52%{transform:translateX(-2px)}64%{transform:translateX(2px)}78%{transform:translateX(-1px)}}' +
    '.p3-svg.act-bow{animation:p3ActSpin 1.1s cubic-bezier(.45,.05,.55,.95) both}' +
    '@keyframes p3ActSpin{0%{transform:rotate(0)}100%{transform:rotate(360deg)}}' +
    '.p3-svg.act-headset{animation:p3ActGroove 1.3s ease-in-out both}' +
    '@keyframes p3ActGroove{0%,100%{transform:none}15%{transform:rotate(-6deg)}32%{transform:rotate(6deg)}' +
      '50%{transform:rotate(-5deg)}68%{transform:rotate(5deg)}85%{transform:rotate(-2deg)}}' +
    '.p3-svg.act-bg-star{animation:p3ActJump 1.15s ease-in-out both}' +
    '@keyframes p3ActJump{0%,100%{transform:none}20%{transform:translateY(-11px) scale(1.04,.97)}' +
      '38%{transform:translateY(0) scale(.98,1.03)}58%{transform:translateY(-7px) scale(1.03,.98)}' +
      '76%{transform:translateY(0)}}' +
    /* 新增四件衣服的动作：比头饰更"体面"一点，都是整理衣服那一类的小动作 */
    '.p3-svg.act-vest{animation:p3ActTidy 1.15s ease-in-out both}' +
    '@keyframes p3ActTidy{0%,100%{transform:none}22%{transform:rotate(-3deg) translateY(1px)}' +
      '46%{transform:rotate(2.5deg) translateY(-1px)}70%{transform:rotate(-1deg)}}' +
    '.p3-svg.act-overall{animation:p3ActPat 1.2s ease-in-out both}' +
    '@keyframes p3ActPat{0%,100%{transform:none}18%{transform:translateY(-3px) rotate(-2deg)}' +
      '34%{transform:translateY(2px) rotate(0deg)}52%{transform:translateY(-2px) rotate(2deg)}' +
      '70%{transform:translateY(1px)}}' +
    '.p3-svg.act-skirt{animation:p3ActTwirl 1.25s ease-in-out both}' +
    '@keyframes p3ActTwirl{0%,100%{transform:none}30%{transform:rotate(-9deg) scale(.98)}' +
      '62%{transform:rotate(9deg) scale(1.02,.98)}84%{transform:rotate(-3deg)}}' +
    '.p3-svg.act-robe{animation:p3ActScholar 1.3s ease-in-out both}' +
    '@keyframes p3ActScholar{0%,100%{transform:none}20%{transform:translateY(-4px) rotate(-2deg)}' +
      '40%{transform:translateY(1px) rotate(1.5deg)}60%{transform:translateY(-2px) rotate(-1deg)}' +
    '@media (prefers-reduced-motion:reduce){.p3-breathe,.p3-head,.p3-eye,.p3-ear,.p3-tail{animation:none}' +
      '.p3-svg{animation:none!important}}' +
    '</style>';

  /* 相位错开：让每只宠物的眨眼/呼吸节奏不同，画面才不呆板 */
  function _p3delay(seed) {
    return '-' + ((_p3seq * 7 % 11) * 0.41 + seed).toFixed(2) + 's';
  }
  /* {{p}} 一律展开成「实例号 + 连字符」：装扮里的写法是 url(#{{p}}red)，
     而渐变 id 是 p + '-red'，少这个连字符会让所有渐变引用落空
     —— Chrome 对无效 paint server 按透明处理，图形直接消失（2026-11-02 修）。 */
  function _p3sub(str, p) {
    return String(str || '').replace(/\{\{p\}\}/g, p + '-');
  }
  function _p3lin(id, c1, c2, c3) {
    return '<linearGradient id="' + id + '" x1=".12" y1="0" x2=".82" y2="1">' +
      '<stop offset="0" stop-color="' + c1 + '"/><stop offset="52%" stop-color="' + c2 + '"/>' +
      '<stop offset="100%" stop-color="' + c3 + '"/></linearGradient>';
  }
  function _p3rad(id, c1, c2, c3) {
    return '<radialGradient id="' + id + '" cx="36%" cy="28%" r="84%">' +
      '<stop offset="0" stop-color="' + c1 + '"/><stop offset="55%" stop-color="' + c2 + '"/>' +
      '<stop offset="100%" stop-color="' + c3 + '"/></radialGradient>';
  }

  /* 宠物本体渐变（按主色自动推导） */
  function p3Defs(p, pal, o) {
    var d = '<defs>' +
      '<radialGradient id="' + p + '-hg" cx="34%" cy="24%" r="86%">' +
        '<stop offset="0" stop-color="' + pal.hi + '"/>' +
        '<stop offset="46%" stop-color="' + pal.main + '"/>' +
        '<stop offset="100%" stop-color="' + pal.dark + '"/></radialGradient>' +
      '<radialGradient id="' + p + '-bg" cx="36%" cy="18%" r="94%">' +
        '<stop offset="0" stop-color="' + pal.main + '"/>' +
        '<stop offset="56%" stop-color="' + pal.mid + '"/>' +
        '<stop offset="100%" stop-color="' + pal.deep + '"/></radialGradient>' +
      /* 高光：纯白径向 → 透明，叠在形体上就是"釉面反光" */
      '<radialGradient id="' + p + '-gl" cx="50%" cy="50%" r="50%">' +
        '<stop offset="0" stop-color="#fff" stop-opacity=".9"/>' +
        '<stop offset="100%" stop-color="#fff" stop-opacity="0"/></radialGradient>' +
      /* 底部环境反光：桌面把光反上来，暗部才不"死" */
      '<radialGradient id="' + p + '-rim" cx="50%" cy="66%" r="52%">' +
        '<stop offset="0" stop-color="' + pal.hi + '" stop-opacity=".7"/>' +
        '<stop offset="100%" stop-color="' + pal.hi + '" stop-opacity="0"/></radialGradient>' +
      /* 边缘暗角：球体边缘自然衰减，这是体积感的最后一层。
         起始点放到 64%、末端只压到 .2 —— 再重就显脏，Q 版角色要"透"。 */
      '<radialGradient id="' + p + '-vg" cx="50%" cy="42%" r="63%">' +
        '<stop offset="64%" stop-color="#2A1A10" stop-opacity="0"/>' +
        '<stop offset="100%" stop-color="#2A1A10" stop-opacity=".2"/></radialGradient>' +
      /* 接触阴影 */
      '<radialGradient id="' + p + '-sh" cx="50%" cy="50%" r="50%">' +
        '<stop offset="0" stop-color="#6B4A2E" stop-opacity=".36"/>' +
        '<stop offset="66%" stop-color="#6B4A2E" stop-opacity=".13"/>' +
        '<stop offset="100%" stop-color="#6B4A2E" stop-opacity="0"/></radialGradient>' +
      /* 眼珠球面：中心透亮、边缘压暗 */
      '<radialGradient id="' + p + '-eye" cx="38%" cy="32%" r="72%">' +
        '<stop offset="0" stop-color="#fff" stop-opacity=".38"/>' +
        '<stop offset="62%" stop-color="#fff" stop-opacity="0"/></radialGradient>' +
      /* 肚子：中心亮、往下压暗 —— 平涂的肚子像贴了张纸，渐变才鼓得起来 */
      '<radialGradient id="' + p + '-bl" cx="40%" cy="24%" r="88%">' +
        '<stop offset="0" stop-color="' + _mix(_hex(o && o.belly ? o.belly : pal.hi), _W, .72) + '"/>' +
        '<stop offset="58%" stop-color="' + (o && o.belly ? o.belly : pal.mid) + '"/>' +
        '<stop offset="100%" stop-color="' + _mix(_hex(o && o.belly ? o.belly : pal.dark), _K, .16) + '"/>' +
      '</radialGradient>' +
      /* 腮红：中心有色、边缘化开 —— 硬边圆点会像贴了两块红纸 */
      '<radialGradient id="' + p + '-blsh" cx="50%" cy="50%" r="50%">' +
        '<stop offset="0" stop-color="#F98A8A" stop-opacity=".62"/>' +
        '<stop offset="68%" stop-color="#F98A8A" stop-opacity=".26"/>' +
        '<stop offset="100%" stop-color="#F98A8A" stop-opacity="0"/>' +
      '</radialGradient>' +
      '</defs>';
    if (o && o.muzzle) {
      d = d.replace('</defs>',
        '<radialGradient id="' + p + '-mz" cx="42%" cy="28%" r="88%">' +
        '<stop offset="0" stop-color="#FFFFFF"/>' +
        '<stop offset="52%" stop-color="' + o.muzzle + '"/>' +
        '<stop offset="100%" stop-color="' + _mix(_hex(o.muzzle), _K, .18) + '"/></radialGradient></defs>');
    }
    return d;
  }

  /* 装扮渐变：金 / 绸 / 宝石 / 镜片 / 木 …… 与宠物本体同一套光照方向 */
  function p3DecoDefs(p) {
    return '<defs>' +
      _p3lin(p + '-gold', '#FFF0BE', '#F2C24E', '#B98818') +
      _p3lin(p + '-red', '#FF9C86', '#DE4B36', '#9C2A1B') +
      _p3lin(p + '-redDark', '#D9543E', '#B33425', '#7A2114') +
      _p3lin(p + '-pink', '#FFD3E6', '#F582B2', '#C8558A') +
      _p3rad(p + '-pinkDeep', '#FFC2DC', '#EE6BA3', '#B4467C') +
      _p3lin(p + '-blue', '#BFE0FF', '#5B8DEF', '#2F5CB5') +
      _p3lin(p + '-blueDark', '#7FA8F2', '#3E6FD8', '#23478F') +
      _p3lin(p + '-wood', '#B98A5C', '#8A5C34', '#5B3A1E') +
      _p3rad(p + '-lens', '#5A6B7D', '#232F3C', '#10171F') +
      _p3rad(p + '-cream', '#FFFFFF', '#F7EFE2', '#D9CDBC') +
      _p3rad(p + '-gemR', '#FF9B8A', '#DE4B36', '#8E2114') +
      _p3rad(p + '-gemB', '#9EC6FF', '#5B8DEF', '#2A4FA0') +
      _p3rad(p + '-gemG', '#A8EDBC', '#4CAF6E', '#256B40') +
      '<radialGradient id="' + p + '-halo" cx="50%" cy="50%" r="50%">' +
        '<stop offset="0" stop-color="#8E6BE0" stop-opacity=".30"/>' +
        '<stop offset="72%" stop-color="#8E6BE0" stop-opacity=".09"/>' +
        '<stop offset="100%" stop-color="#8E6BE0" stop-opacity="0"/></radialGradient>' +
      /* 装扮投影：拟物感的关键一层 —— 帽子/眼镜是"搭"在宠物身上的实体，
         没有落在毛上的影子就会像贴纸。 */
      '<filter id="' + p + '-ds" x="-35%" y="-35%" width="180%" height="180%">' +
        '<feDropShadow dx="0" dy="1.6" stdDeviation="1.5" flood-color="#4A2C14" flood-opacity=".40"/>' +
      '</filter>' +
      '</defs>';
  }

  /* 眼睛：深色球体 + 主高光 + 次反光点，靠中心坐标做眨眼（绕 0,0 压扁）。
     2026-11-02 二次调整：眼睛从「头中线」下移到脸部中下 1/3（y=55.5），
     半径放大到 4.9×5.6 —— Q 版角色的可爱感几乎全押在「大眼 + 低眼位」上，
     上一版眼位偏高、眼偏小，整只看起来像个面无表情的雪人。 */
  function _p3eye(p, x, y, col, k) {
    k = k || 1;
    /* 2026-11-02 卡通化：眼轴再放大一圈（5.2×6.1 → 6.0×7.0）。
       上一版偏"拟物"——体积、光泽都到位，但眼睛相对头还是小，
       整体像精致的摆件而不是卡通角色。卡通的第一语言就是大眼睛。 */
    var rx = 6.0 * k, ry = 7.0 * k;
    /* 苹果拟物表情的眼睛：略微内倾的椭圆 + 一枚大高光 + 一枚小反光。
       内倾（左眼逆时针、右眼顺时针）是「看着你」的关键，正圆会显得呆。 */
    var tilt = x < 60 ? -6 : 6;
    return '<g transform="translate(' + x + ',' + y + ')">' +
      '<g class="p3-eye" style="animation-delay:' + _p3delay(1.3) + '">' +
      '<g transform="rotate(' + tilt + ')">' +
        '<ellipse rx="' + rx + '" ry="' + ry + '" fill="' + col + '"/>' +
        '<ellipse rx="' + rx + '" ry="' + ry + '" fill="url(#' + p + '-eye)"/>' +
        '<ellipse cx="' + (-rx * .36).toFixed(2) + '" cy="' + (-ry * .38).toFixed(2) +
          '" rx="' + (rx * .44).toFixed(2) + '" ry="' + (ry * .38).toFixed(2) +
          '" fill="#fff" opacity=".96"/>' +
        '<ellipse cx="' + (rx * .34).toFixed(2) + '" cy="' + (ry * .36).toFixed(2) +
          '" rx="' + (rx * .2).toFixed(2) + '" ry="' + (ry * .17).toFixed(2) +
          '" fill="#fff" opacity=".5"/>' +
      '</g></g></g>';
  }

  /* 耳朵：不同物种靠耳型区分，全部画在头球之下（耳根被头部盖住更自然） */
  function p3Ears(o, p, pal) {
    var s = '';
    var earWrap = function (ox, oy, inner) {
      return '<g transform="translate(' + ox + ',' + oy + ')">' +
        '<g class="p3-ear" style="animation-delay:' + _p3delay(2.1) + '">' +
        '<g transform="translate(' + (-ox) + ',' + (-oy) + ')">' + inner + '</g></g></g>';
    };
    if (o.ear === 'cat') {
      s += earWrap(42, 38, '<path d="M39 40 35 17l19 11Z" fill="url(#' + p + '-hg)"/>' +
        '<path d="M42 36.5 40 24l10.5 6.5Z" fill="' + pal.main + '" opacity=".55"/>');
      s += earWrap(78, 38, '<path d="M81 40 85 17 66 28Z" fill="url(#' + p + '-hg)"/>' +
        '<path d="M78 36.5 80 24l-10.5 6.5Z" fill="' + pal.main + '" opacity=".55"/>');
    } else if (o.ear === 'drop') {
      /* 柴犬：立起来的尖耳（上一版是两个圆椭圆，看着像垂耳兔） */
      s += earWrap(38, 40, '<path d="M34 45 33 24q0-5 5-2l14 17Z" fill="url(#' + p + '-hg)"/>' +
        '<path d="M37 41 37 29l8 9.5Z" fill="' + pal.main + '" opacity=".5"/>');
      s += earWrap(82, 40, '<path d="M86 45 87 24q0-5-5-2l-14 17Z" fill="url(#' + p + '-hg)"/>' +
        '<path d="M83 41 83 29l-8 9.5Z" fill="' + pal.main + '" opacity=".5"/>');
    } else if (o.ear === 'lop') {
      /* 垂耳兔：耳朵不是竖着的长椭圆，而是从头顶两侧**垂下来**贴在脸边。
         上一版和独角兽共用 'long'（竖立旋转 ±9°），一只垂耳兔长着竖耳朵，
         名字和长相直接对不上。 */
      s += earWrap(33, 56, '<ellipse cx="31" cy="57" rx="7.8" ry="19.4" fill="url(#' + p + '-hg)" transform="rotate(11 31 57)"/>' +
        '<ellipse cx="31" cy="58" rx="3.4" ry="12.4" fill="#F6BFBF" opacity=".72" transform="rotate(11 31 58)"/>');
      s += earWrap(87, 56, '<ellipse cx="89" cy="57" rx="7.8" ry="19.4" fill="url(#' + p + '-hg)" transform="rotate(-11 89 57)"/>' +
        '<ellipse cx="89" cy="58" rx="3.4" ry="12.4" fill="#F6BFBF" opacity=".72" transform="rotate(-11 89 58)"/>');
    } else if (o.ear === 'long') {
      s += earWrap(46, 34, '<ellipse cx="45" cy="26" rx="7.4" ry="19" fill="url(#' + p + '-hg)" transform="rotate(-9 45 26)"/>' +
        '<ellipse cx="45" cy="26" rx="3.4" ry="12" fill="#F6BFBF" opacity=".75" transform="rotate(-9 45 26)"/>');
      s += earWrap(74, 34, '<ellipse cx="75" cy="26" rx="7.4" ry="19" fill="url(#' + p + '-hg)" transform="rotate(9 75 26)"/>' +
        '<ellipse cx="75" cy="26" rx="3.4" ry="12" fill="#F6BFBF" opacity=".75" transform="rotate(9 75 26)"/>');
    } else if (o.ear === 'round') {
      /* 熊猫圆耳：用眼圈色（黑）而不是主色 —— 上一版用主色渐变，
         白身熊猫长出一对白耳朵，看着像戴了个耳罩。 */
      var rc = o.patch || pal.mid;
      s += '<circle cx="40" cy="33.5" r="10" fill="' + rc + '"/>' +
           '<circle cx="80" cy="33.5" r="10" fill="' + rc + '"/>' +
           '<ellipse cx="37" cy="30.5" rx="3.4" ry="2.2" fill="#fff" opacity=".16" transform="rotate(-24 37 30.5)"/>' +
           '<ellipse cx="77" cy="30.5" rx="3.4" ry="2.2" fill="#fff" opacity=".16" transform="rotate(-24 77 30.5)"/>';
    } else if (o.ear === 'crest') {
      s += '<path d="M54 30q-2-11 6-12 3 5 3 11Z" fill="' + pal.hi + '"/>' +
           '<path d="M66 30q2-11-6-12-3 5-3 11Z" fill="' + pal.hi + '"/>';
    } else if (o.ear === 'horn') {
      s += '<path d="M43 37q-11-7-7-19 6-1 10 5 3 5 3 10Z" fill="' + pal.dark + '"/>' +
           '<path d="M77 37q11-7 7-19-6-1-10 5-3 5-3 10Z" fill="' + pal.dark + '"/>' +
           '<path d="M45 33q-6-5-4-12" stroke="' + pal.hi + '" stroke-opacity=".55" stroke-width="2" fill="none" stroke-linecap="round"/>' +
           '<path d="M75 33q6-5 4-12" stroke="' + pal.hi + '" stroke-opacity=".55" stroke-width="2" fill="none" stroke-linecap="round"/>';
    }
    return s;
  }

  /* 面部：脸盘 / 口鼻区 / 眼圈 / 眼 / 鼻 / 嘴 / 胡须 / 腮红
     2026-11-02 二次调整：五官整体下移 4~5 个单位并放大眼部，
     鼻、嘴之间留出呼吸间距，避免上一版「眼睛贴着额头、鼻子压着下巴」。 */
  function p3Face(o, p, pal, eyeC) {
    var s = '';
    /* 深色物种（企鹅）：先铺一块浅色脸盘，否则深色眼睛融进羽毛里完全看不见 */
    if (o.face) {
      s += '<ellipse cx="60" cy="57.5" rx="16.8" ry="17.6" fill="' + o.face + '"/>' +
           '<ellipse cx="60" cy="57.5" rx="16.8" ry="17.6" fill="url(#' + p + '-vg)" opacity=".3"/>';
    }
    if (o.muzzle) {
      s += '<ellipse cx="60" cy="66" rx="11.5" ry="8.6" fill="url(#' + p + '-mz)"/>' +
           '<ellipse cx="60" cy="66" rx="11.5" ry="8.6" fill="url(#' + p + '-vg)" opacity=".28"/>';
    }
    if (o.patch) {                      /* 熊猫黑眼圈（眼位跟普通宠物对齐在 55 附近） */
      s += '<ellipse cx="50" cy="54" rx="8.2" ry="9.4" fill="' + o.patch + '" transform="rotate(-14 50 54)"/>' +
           '<ellipse cx="70" cy="54" rx="8.2" ry="9.4" fill="' + o.patch + '" transform="rotate(14 70 54)"/>' +
           '<ellipse cx="49.6" cy="54" rx="5" ry="5.6" fill="#fff"/>' +
           '<ellipse cx="70.4" cy="54" rx="5" ry="5.6" fill="#fff"/>' +
           '<g transform="translate(50,54.6)"><g class="p3-eye" style="animation-delay:' + _p3delay(1.3) + '">' +
             '<circle r="3.7" fill="#141210"/><circle cx="-1.2" cy="-1.3" r="1.35" fill="#fff" opacity=".94"/>' +
             '<circle cx="1.1" cy="1.2" r=".7" fill="#fff" opacity=".5"/>' +
           '</g></g>' +
           '<g transform="translate(70,54.6)"><g class="p3-eye" style="animation-delay:' + _p3delay(1.3) + '">' +
             '<circle r="3.7" fill="#141210"/><circle cx="-1.2" cy="-1.3" r="1.35" fill="#fff" opacity=".94"/>' +
             '<circle cx="1.1" cy="1.2" r=".7" fill="#fff" opacity=".5"/>' +
           '</g></g>';
      s += '<ellipse cx="60" cy="66.5" rx="4.2" ry="3.1" fill="#221C18"/>' +
           '<ellipse cx="58.6" cy="65.3" rx="1.25" ry=".9" fill="#fff" opacity=".55"/>' +
           '<path d="M56 71.5q4 3.4 8 0" stroke="#221C18" stroke-width="1.6" fill="none" stroke-linecap="round"/>';
    } else {
      s += _p3eye(p, 50, 54.2, eyeC, 1) + _p3eye(p, 70, 54.2, eyeC, 1);
    }
    if (o.beak) {                        /* 企鹅喙：起点压到眼睛下缘之下，不挡眼 */
      s += '<path d="M54 62h12l-6 8Z" fill="url(#' + p + '-beak)"/>' +
           '<path d="M55.6 62.6h8.8L60 66.6Z" fill="#FFC66B" opacity=".6"/>';
    } else if (o.ear === 'cat') {
      s += '<path d="M57 64.4q3-2 6 0-1.4 3.4-3 3.4t-3-3.4Z" fill="#D9785C"/>' +
           '<ellipse cx="58.4" cy="64.6" rx="1.1" ry=".8" fill="#FFC0A6" opacity=".8"/>' +
           '<path d="M55.5 71.5q4.5 3.4 9 0" stroke="' + eyeC + '" stroke-width="1.7" fill="none" stroke-linecap="round"/>' +
           '<path d="M31 59.5h13M31 65.5h13M76 59.5h13M76 65.5h13" stroke="#C98F5E" stroke-opacity=".7" stroke-width="1.5" stroke-linecap="round"/>';
    } else if (o.brow) {                 /* 柴犬：白眉点 + 黑鼻 */
      s += '<ellipse cx="60" cy="65.6" rx="4.3" ry="3.3" fill="#2E2416"/>' +
           '<ellipse cx="58.8" cy="64.4" rx="1.35" ry="1" fill="#fff" opacity=".55"/>' +
           '<path d="M55.5 71.8q4.5 3.4 9 0" stroke="' + eyeC + '" stroke-width="1.7" fill="none" stroke-linecap="round"/>' +
           '<ellipse cx="47" cy="48.5" rx="2.7" ry="1.9" fill="#FFF6E4" opacity=".92"/>' +
           '<ellipse cx="73" cy="48.5" rx="2.7" ry="1.9" fill="#FFF6E4" opacity=".92"/>';
    } else if (o.mane) {                 /* 独角兽：金角 + 彩虹鬃毛 */
      s += '<path d="M56.5 30 60 11l3.5 19Z" fill="url(#' + p + '-horn)"/>' +
           '<path d="M58.4 27 60 14.5l1.6 12.5Z" fill="#FFF6D8" opacity=".7"/>' +
           '<path d="M44 34q-7-11 2-17 6 6 8 13Z" fill="#F6BFE0"/>' +
           '<path d="M76 34q7-11-2-17-6 6-8 13Z" fill="#C9A6F2"/>' +
           '<ellipse cx="60" cy="65" rx="3.7" ry="3" fill="#F08EB0"/>' +
           '<ellipse cx="59" cy="63.9" rx="1.2" ry=".8" fill="#fff" opacity=".7"/>' +
           '<path d="M55.5 71q4.5 3.2 9 0" stroke="' + eyeC + '" stroke-width="1.7" fill="none" stroke-linecap="round"/>';
    } else if (o.wing) {                 /* 小龙：鼻头 + 小虎牙 */
      s += '<ellipse cx="60" cy="65.4" rx="4.5" ry="3.5" fill="#8E4A22"/>' +
           '<ellipse cx="58.7" cy="64.2" rx="1.4" ry="1" fill="#FFD9A0" opacity=".65"/>' +
           '<path d="M55 71.5q5 3.6 10 0" stroke="' + eyeC + '" stroke-width="1.7" fill="none" stroke-linecap="round"/>' +
           '<path d="M57 71.6 58 75l1.4-3.2" fill="#FFF8EC"/>' +
           '<path d="M63 71.6 62 75l-1.4-3.2" fill="#FFF8EC"/>';
    } else {                             /* 默认（兔子 / 恐龙）：鼻色由主色推深，不写死 */
      var noseC = _mix(_hex(o.c), _K, o.ear === 'crest' ? .45 : .55);
      s += '<ellipse cx="60" cy="65" rx="3.4" ry="2.7" fill="' + noseC + '"/>' +
           '<ellipse cx="58.9" cy="64.1" rx="1.1" ry=".8" fill="#fff" opacity=".5"/>' +
           '<path d="M54 71q6 3.8 12 0" stroke="' + eyeC + '" stroke-width="1.7" fill="none" stroke-linecap="round"/>';
    }
    if (o.blush) {
      /* 卡通化的另一件必备品：腮红要看得见。原来 7×4.4 且透明度低，
         在 40px 的小图里基本消失；现在放大到 8.8×5.6。 */
      s += '<ellipse cx="37.5" cy="64.5" rx="8.8" ry="5.6" fill="url(#' + p + '-blsh)"/>' +
           '<ellipse cx="82.5" cy="64.5" rx="8.8" ry="5.6" fill="url(#' + p + '-blsh)"/>';
    }
    return s;
  }

  function p3Tail(o, p, pal, ox) {
    if (!o.tail || o.tail === 'none') return '';
    var bx = ox || 84;
    var inner = '';
    if (o.tail === 'curl') {
      inner = '<path d="M84 84q13 0 10-13" stroke="url(#' + p + '-bg)" stroke-width="8" fill="none" stroke-linecap="round"/>' +
              '<path d="M84 84q13 0 10-13" stroke="url(#' + p + '-vg)" stroke-width="8" fill="none" stroke-linecap="round" opacity=".6"/>';
    } else if (o.tail === 'puff') {
      inner = '<circle cx="87" cy="96" r="7.5" fill="url(#' + p + '-hg)"/>' +
              '<ellipse cx="85" cy="93.5" rx="3" ry="2.2" fill="#fff" opacity=".55"/>';
    } else {
      inner = '<path d="M84 88q14 2 15 12-6-2-12 1Z" fill="url(#' + p + '-bg)"/>' +
              '<path d="M84 88q14 2 15 12-6-2-12 1Z" fill="url(#' + p + '-vg)" opacity=".5"/>';
    }
    return '<g transform="translate(' + bx + ',86)"><g class="p3-tail" style="animation-delay:' +
      _p3delay(0.6) + '"><g transform="translate(-84,-86)">' + inner + '</g></g></g>';
  }

  function p3Wing(o, p, pal) {
    if (!o.wing) return '';
    /* 翅膀要**从身体两侧往下探**才看得见：上一版画在 x=20~40 / y=48~78，
       正好整片压在头（r26 圆心 53）下面，等于没长翅膀。
       现在改到身体下缘外侧（y 84~102），头够不到，只被身体压住根部。 */
    return '<path d="M44 84q-22 1-29 16 21 5 32-6Z" fill="' + pal.mid + '"/>' +
           '<path d="M44 84q-22 1-29 16 21 5 32-6Z" fill="url(#' + p + '-vg)" opacity=".38"/>' +
           /* 两道翅脉：没有它，同色的翅膀和背棘/尾巴分不出来 */
           '<path d="M40 86 22 97M39 92l-9 7" stroke="' + pal.hi + '" stroke-opacity=".5" stroke-width="1.7" fill="none" stroke-linecap="round"/>';
  }

  /* 脚掌：贴在身体下缘的两个小圆脚。
     上一版只画了熊猫的一对「黑手臂」，脚是完全缺的 —— 整只看像悬空的团子。
     脚画在身体之前（上半截被身体压住），只露出踩地的那一半，
     再加一点暗部与顶部高光，才不像贴上去的两个黑点。 */
  function p3Feet(o, p, pal, footY, footX) {
    if (o.beak) return '';                 /* 企鹅的橙鸭掌在 p3Limbs 里，形状不同 */
    var c = o.patch || ('url(#' + p + '-bg)');
    var fy = footY || 104.5, fx = footX || 13;
    var s = '';
    [[60 - fx, fy], [60 + fx, fy]].forEach(function (pt) {
      s += '<ellipse cx="' + pt[0].toFixed(1) + '" cy="' + pt[1].toFixed(1) + '" rx="8.6" ry="6" fill="' + c + '"/>' +
           '<ellipse cx="' + pt[0].toFixed(1) + '" cy="' + (pt[1] + 1).toFixed(1) + '" rx="8.6" ry="6" fill="url(#' + p + '-vg)" opacity=".32"/>' +
           '<ellipse cx="' + (pt[0] - 1.6).toFixed(1) + '" cy="' + (pt[1] - 1.8).toFixed(1) +
             '" rx="3.6" ry="2.1" fill="#fff" opacity=".25"/>';
    });
    return s;
  }

  /* 背棘：恐龙的辨识点，画在身体之前（根部被身体压住）。
     原来横着排在身体顶上，结果全被头挡住、只露出一颗绿揪揪 ——
     改到左背外缘，朝外斜出，半截压在身体下，才看得出是"一排棘"。 */
  function p3Back(o) {
    if (!o.spikes) return '';
    return [[40, 77, 4.6], [37.5, 86, 5.4], [39.5, 95, 4.6]].map(function (t) {
      return '<path d="M' + (t[0] + 3) + ' ' + (t[1] - t[2] * .75) + 'L' + (t[0] - t[2] * 1.6) + ' ' + t[1] +
        'L' + (t[0] + 3) + ' ' + (t[1] + t[2] * .75) + 'Z" fill="#3F9E5E"/>';
    }).join('');
  }

  /* 物种专属道具：buddy 图鉴那种「一只一个记忆点」的做法 ——
     柴犬的项圈铜铃、兔子的胡萝卜、企鹅的领结、熊猫的竹子、独角兽的星星项圈。
     画在身体之后、头之前，上沿自然被头压住。 */
  function p3Props(o, p) {
    var s = '';
    if (o.id === 'dog') {
      s += '<path d="M42 74q18 10 36 0" fill="none" stroke="#8E1F18" stroke-opacity=".4" stroke-width="5.6" stroke-linecap="round" transform="translate(0,1.8)"/>' +
           '<path d="M42 74q18 10 36 0" fill="none" stroke="#C0392B" stroke-width="5.4" stroke-linecap="round"/>' +
           '<circle cx="60" cy="83" r="4.2" fill="url(#' + p + '-gold)"/>' +
           '<circle cx="58.7" cy="81.6" r="1.3" fill="#FFF8E0" opacity=".9"/>';
    } else if (o.id === 'rabbit') {
      /* 胡萝卜要"被抱着"而不是"插在旁边"：上一版放在 x=85，
         兔子的身体右缘才 80.6，整根悬空，看着像身上长了根胡萝卜。 */
      s += '<g transform="translate(80,94) rotate(24) scale(1.12)">' +
           '<path d="M-4.6 0L4.6 0 0 15Z" fill="url(#' + p + '-red)"/>' +
           '<path d="M-4.6 0L0 0 0 15Z" fill="#fff" fill-opacity=".25"/>' +
           '<path d="M0 1q-4-6-8-5 3 3 4 6Z" fill="#59A868"/>' +
           '<path d="M0 1q4-6 8-5-3 3-4 6Z" fill="#6CBB78"/>' +
           '</g>';
    } else if (o.id === 'penguin') {
      s += '<path d="M60 76l-7.4-4.6v9.2Z" fill="url(#' + p + '-redDark)"/>' +
           '<path d="M60 76l7.4-4.6v9.2Z" fill="url(#' + p + '-redDark)"/>' +
           '<circle cx="60" cy="76" r="2.7" fill="url(#' + p + '-red)"/>';
    } else if (o.id === 'panda') {
      /* 竹子挪到左爪位置（cx 42）：原来在 x=29 是身体外侧，
         看起来像插在地上的竹竿，不像熊猫抱着。 */
      s += '<g transform="translate(38,96) rotate(-7)">' +
           '<path d="M0 18V-9" stroke="#4E8C4C" stroke-width="4.6" stroke-linecap="round"/>' +
           '<path d="M0 18V-9" stroke="#7CC07A" stroke-width="1.8" stroke-linecap="round" opacity=".8"/>' +
           '<path d="M1-3q8-1 10-8-8 0-10 8Z" fill="#5FA85C"/>' +
           '<path d="M-1 6q-8-1-10-8 8 0 10 8Z" fill="#4E8C4C"/>' +
           '</g>';
    } else if (o.id === 'unicorn') {
      s += '<path d="M44 74q16 9 32 0" fill="none" stroke="#D8B24A" stroke-width="3.4" stroke-linecap="round"/>' +
           '<path d="M60 78l2 3.7 4.1.6-3 2.9.7 4-3.8-2-3.8 2 .7-4-3-2.9 4.1-.6Z" fill="url(#' + p + '-gold)"/>';
    } else if (o.id === 'dragon') {
      s += '<path d="M94 97l7-1.4-2.6 5.2Z" fill="#D9663A"/>' +
           '<path d="M96 97.4l4.4-.9" stroke="#FFB27A" stroke-width="1.2" stroke-linecap="round" opacity=".7"/>';
    }
    return s;
  }

  function p3Limbs(o, p, pal) {
    var s = '';
    if (o.patch) {   /* 熊猫黑四肢 */
      s += '<ellipse cx="42" cy="90" rx="7" ry="10" fill="' + o.patch + '" transform="rotate(20 42 90)"/>' +
           '<ellipse cx="78" cy="90" rx="7" ry="10" fill="' + o.patch + '" transform="rotate(-20 78 90)"/>';
    }
    if (o.beak) {    /* 企鹅橙脚 + 鳍 */
      s += '<ellipse cx="47" cy="104" rx="9" ry="4.2" fill="url(#' + p + '-beak)"/>' +
           '<ellipse cx="73" cy="104" rx="9" ry="4.2" fill="url(#' + p + '-beak)"/>' +
           '<ellipse cx="33" cy="86" rx="5.5" ry="12" fill="url(#' + p + '-bg)" transform="rotate(14 33 86)"/>' +
           '<ellipse cx="87" cy="86" rx="5.5" ry="12" fill="url(#' + p + '-bg)" transform="rotate(-14 87 86)"/>';
    }
    return s;
  }

  /** 组装一只 3D 宠物的图形（不含 <svg> 包裹）
      2026-11-02 五次调整：走「苹果拟物」——不描边，全靠光。
      ① 体积四件套：径向渐变本体 + 大柔高光 + 小锐高光 + 边缘暗角；
      ② 头与身体之间加一圈环境遮蔽（AO），头才像压在身体上而不是浮着；
      ③ 肚子、腮红、口鼻区改渐变，平涂会像贴了张纸；
      ④ 底部反光压在暗角之后，下缘才不会"死"。 */
  function p3Art(o, p, pal) {
    var eyeC = o.eye || '#2E2418';
    /* 体型参数：猫是基准，其余物种在 PETS 里覆盖 —— 这是「不像同一个模子刻的」的根 */
    var bw = o.bw || 23.5, bh = o.bh || 19.5, bcy = o.bcy || 88;
    var hr = o.hr || 26, hcy = o.hcy || 53;
    /* 卡通化：整颗头再放大 6%（统一系数，八只一起变），配合放大的眼睛，
       头身比更接近「二头身」的卡通比例。装扮的 fit 用同一个系数，
       所以帽子眼镜会一起放大，不用逐件重画。 */
    var k = (hr / 26) * 1.06;
    var footY = bcy + bh * 0.85, footX = Math.min(15, bw * 0.55);
    var fx2 = (60 - bw * 0.4).toFixed(1), fy2 = (bcy - bh * 0.42).toFixed(1);
    var s = '<ellipse cx="60" cy="' + (bcy + bh).toFixed(1) + '" rx="' + (bw * 1.15).toFixed(1) +
      '" ry="' + (bh * 0.34).toFixed(1) + '" fill="url(#' + p + '-sh)"/>';
    s += p3Tail(o, p, pal, 60 + bw - 1) + p3Wing(o, p, pal);
    /* 呼吸：以脚底 (60,108) 为支点，整只缩放 —— 外面 translate 进去、里面 translate 回来 */
    s += '<g transform="translate(60,108)"><g class="p3-breathe" style="animation-delay:' +
      _p3delay(0) + '"><g transform="translate(-60,-108)">';
    s += p3Feet(o, p, pal, footY, footX);
    s += p3Back(o);
    /* 头身比按物种走：柴犬宽扁、兔子瘦高、企鹅直立、熊猫最圆、恐龙头大身小 */
    s += '<ellipse cx="60" cy="' + bcy + '" rx="' + bw + '" ry="' + bh + '" fill="url(#' + p + '-bg)"/>';
    if (o.belly) {
      s += '<ellipse cx="60" cy="' + (bcy + 3).toFixed(1) + '" rx="' + (bw * 0.64).toFixed(1) +
        '" ry="' + (bh * 0.65).toFixed(1) + '" fill="url(#' + p + '-bl)"/>';
    }
    s += '<ellipse cx="' + fx2 + '" cy="' + fy2 + '" rx="' + (bw * 0.4).toFixed(1) +
      '" ry="' + (bh * 0.29).toFixed(1) + '" fill="url(#' + p + '-gl)" opacity=".42" transform="rotate(-18 ' + fx2 + ' ' + fy2 + ')"/>';
    s += '<ellipse cx="60" cy="' + bcy + '" rx="' + bw + '" ry="' + bh + '" fill="url(#' + p + '-vg)"/>';
    s += '<ellipse cx="60" cy="' + (bcy + bh * 0.56).toFixed(1) + '" rx="' + (bw * 0.55).toFixed(1) +
      '" ry="' + (bh * 0.23).toFixed(1) + '" fill="url(#' + p + '-rim)" opacity=".36"/>';
    s += p3Limbs(o, p, pal);
    s += p3Props(o, p);
    /* 头颈环境遮蔽：没有这一圈，头就是"贴"在身体上的 */
    s += '<ellipse cx="60" cy="' + (hcy + hr - 4) + '" rx="' + (hr * 0.63).toFixed(1) +
      '" ry="' + (hr * 0.21).toFixed(1) + '" fill="#4A2E18" opacity=".15"/>';
    /* 头部：以颈部 (60,99) 为支点轻摆；内部的头‐脸‐光全部按 hr/hcy 缩放平移，
       所以改体型不用再逐个坐标调 */
    s += '<g transform="translate(60,99)"><g class="p3-head" style="animation-delay:' +
      _p3delay(1.1) + '"><g transform="translate(-60,-99)">';
    s += '<g transform="translate(60,' + hcy + ') scale(' + k.toFixed(3) + ') translate(-60,-53)">';
    s += p3Ears(o, p, pal);
    s += '<circle cx="60" cy="53" r="26" fill="url(#' + p + '-hg)"/>';
    s += p3Face(o, p, pal, eyeC);
    /* 双层高光：大而柔（釉面）+ 小而亮（点光），苹果那种硅胶玩具的质感全在这 */
    s += '<ellipse cx="45.5" cy="35" rx="10.4" ry="6.4" fill="url(#' + p + '-gl)" opacity=".62" transform="rotate(-26 45.5 35)"/>';
    s += '<ellipse cx="42.6" cy="31.4" rx="3" ry="1.9" fill="#fff" opacity=".76" transform="rotate(-26 42.6 31.4)"/>';
    s += '<circle cx="60" cy="53" r="26" fill="url(#' + p + '-vg)"/>';
    /* 卡通化的关键一笔：给头加一圈「同色系深色」轮廓。
       注意不是黑描边（那是贴纸），是用主色压暗 60% 的 deep 色、42% 透明，
       看起来像角色自带的一层厚度，而不是贴上去的边。
       位置必须在暗角之后 —— 暗角是径向渐变，会把轮廓吃掉。 */
    s += '<circle cx="60" cy="53" r="26" fill="none" stroke="' + pal.deep +
      '" stroke-opacity=".42" stroke-width="1.5"/>';
    /* 反光放在暗角之后：桌面把光反上来，下缘要"亮一下"，不然暗角会把下巴压死 */
    s += '<ellipse cx="60" cy="73" rx="14" ry="5.2" fill="url(#' + p + '-rim)" opacity=".36"/>';
    /* 顶部边缘光：一圈亮弧，深色宠物靠它从背景里"跳"出来 */
    s += '<path d="M36.4 42.2A26 26 0 0 1 83.6 42.2" fill="none" stroke="#FFF8EC" ' +
         'stroke-opacity=".5" stroke-width="1.8" stroke-linecap="round"/>';
    s += '</g>';                       /* 头部缩放包裹 */
    s += '</g></g></g>';
    s += '</g></g></g>';
    return s;
  }

  /** 合成宠物 SVG：pet 宠物对象或 id，decos 装扮 id 数组（星光舞台垫底）。
      opts.act = true 时给 <svg> 挂上「专属动作」类名（牧场展示用）：
      最后一件装扮决定动作，戴上就能看到宠物做出对应反应。
      外层包 try/catch —— 牧场页一屏要画九只，任何一只出错都不该让整页白屏。 */
  function petSvg(pet, decos, opts) {
    try {
      return _petSvg(pet, decos, opts);
    } catch (e) {
      try { console.error('宠物渲染失败：', e); } catch (x) { /* 老环境无 console */ }
      return '';
    }
  }
  /** 装扮跟随物种头部缩放：帽子和眼镜都是按「头 r26 圆心 53」画的，
      直接套到企鹅（r22.6）或兔子（r24）头上会悬空。用同一个头部变换
      包一层，戴哪只都合头。 */
  function p3DecoFit(o) {
    var hr = o.hr || 26, hcy = o.hcy || 53;
    return ' transform="translate(60,' + hcy + ') scale(' + ((hr / 26) * 1.06).toFixed(3) +
      ') translate(-60,-53)"';
  }
  function _petSvg(pet, decos, opts) {
    var o = (typeof pet === 'string') ? petById(pet) : pet;
    if (!o) return '';
    var p = 'p3' + (++_p3seq);
    var pal = p3Pal(o.c);
    var ds = decos || [];
    var act = (opts && opts.act && ds.length) ? ds[ds.length - 1] : '';
    var s = '<svg class="p3-svg' + (act ? ' act-' + act : '') + '" data-pet="' + o.id + '" ' +
      'viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg" ' +
      'role="img" aria-label="' + o.name + '">' + P3_STYLE + p3Defs(p, pal, o) + p3DecoDefs(p);
    if (o.beak) {  /* 企鹅喙渐变需单独给（本体色阶里没有橙） */
      s = s.replace('</defs>', '<linearGradient id="' + p + '-beak" x1=".2" y1="0" x2=".8" y2="1">' +
        '<stop offset="0" stop-color="#FFD98A"/><stop offset="55%" stop-color="#F5A742"/>' +
        '<stop offset="100%" stop-color="#C97A16"/></linearGradient></defs>');
    }
    if (o.mane) {
      s = s.replace('</defs>', '<linearGradient id="' + p + '-horn" x1=".2" y1="1" x2=".9" y2="0">' +
        '<stop offset="0" stop-color="#C8931F"/><stop offset="52%" stop-color="#F6C453"/>' +
        '<stop offset="100%" stop-color="#FFF3C4"/></linearGradient></defs>');
    }
    ds.forEach(function (id) {
      if (id !== 'bg-star') return;
      var d = decoById(id);
      if (d) s += _p3sub(d.svg, p);
    });
    s += p3Art(o, p, pal);
    var fit = p3DecoFit(o);
    ds.forEach(function (id) {
      if (id === 'bg-star') return;
      var d = decoById(id);
      if (d) s += '<g filter="url(#' + p + '-ds)"><g' + fit + '>' + _p3sub(d.svg, p) + '</g></g>';
    });
    return s + '</svg>';
  }

  /** 单个装扮的独立 SVG（装备栏按钮用，按各自的 box 紧贴图形裁剪，图标才够大） */
  function decoSvg(d) {
    if (!d) return '';
    try {
      var p = 'dc' + (++_p3seq);
      return '<svg viewBox="' + (d.box || '20 4 80 104') + '" xmlns="http://www.w3.org/2000/svg">' +
        p3DecoDefs(p) + '<g filter="url(#' + p + '-ds)">' + _p3sub(d.svg, p) + '</g></svg>';
    } catch (e) {
      try { console.error('装扮渲染失败：', e); } catch (x) { /* ignore */ }
      return '';
    }
  }

  /* ---------------- 宠物小像（顶栏 / 排行榜 / 牧场卡片共用，2026-11-05 晚） ----------------
     以前三处各写各的：顶栏只塞一只 SVG、排行榜也只塞 SVG，背景和特效
     只画在牧场大舞台上 —— 同一个学生在牧场里「戴帽子 + 星空」，榜上却是
     一只光秃秃的宠物，看着像数据没同步。现在统一走这里：
     背景 = 牧场背景渐变，特效 = 缩小的粒子层（CSS 在 /typing/pet-ui.css）。 */
  function fxMini(fx) {
    if (!fx || !effectById(fx)) return '';
    var out = '<span class="pch-fx pch-' + fx + '">', i;
    if (fx === 'sparkle') {
      for (i = 0; i < 5; i++)
        out += '<i style="left:' + (10 + i * 18) + '%;top:' + (12 + ((i * 27) % 60)) +
          '%;animation-delay:' + (i * .42) + 's">✨</i>';
    } else if (fx === 'bubbles') {
      for (i = 0; i < 4; i++)
        out += '<i class="bub" style="left:' + (14 + i * 22) + '%;bottom:' +
          (5 + (i % 2) * 7) + 'px;animation-delay:' + (i * .9) + 's"></i>';
    } else if (fx === 'hearts') {
      for (i = 0; i < 3; i++)
        out += '<i style="left:' + (18 + i * 26) + '%;bottom:12%;animation-delay:' +
          (i * .8) + 's">💖</i>';
    } else if (fx === 'snow') {
      for (i = 0; i < 5; i++)
        out += '<i style="left:' + (7 + i * 20) + '%;top:0;animation-delay:' +
          (i * .72) + 's">❄</i>';
    }
    return out + '</span>';
  }

  /** 生成一整个小像：背景（可选）+ 特效（可选）+ 宠物本体（没有就爪印）
      o = {pet, decos, bg, fx, cls, opts, title}
      cls 决定尺寸（pet-mini / lb-pet …），样式各自在外层定义。 */
  function petTile(o) {
    o = o || {};
    var cls = o.cls || 'lb-pet';
    var bg = o.bg ? bgById(o.bg) : null;
    var st = (bg && bg.id) ? ' style="background:' + bg.css + '"' : '';
    var inner = fxMini(o.fx);
    /* 宠物 id 必须在图鉴里，否则渲染出空白小像（老档案里可能有已下线的 id） */
    inner += (o.pet && petById(o.pet))
      ? petSvg(o.pet, o.decos || [], o.opts || {})
      : '<span class="pt-empty">🐾</span>';
    var ttl = o.title ? ' title="' + String(o.title).replace(/"/g, '&quot;') + '"' : '';
    return '<span class="' + cls + ' pet-tile"' + ttl + st + '>' + inner + '</span>';
  }

  return {
    STAGES: STAGES, stageById: stageById, KEY_LESSONS: KEY_LESSONS,
    EN_WORDS: EN_WORDS, EN_SENT: EN_SENT,
    CN_WORDS: CN_WORDS, CN_SENT: CN_SENT,
    S4_MAP: S4_MAP, S4_TOPIC: S4_TOPIC,
    S7_MAP: S7_MAP, S7_TOPIC: S7_TOPIC,
    S8_MAP: S8_MAP, S8_TOPIC: S8_TOPIC,
    PETS: PETS, DECOS: DECOS, TITLES: TITLES, PET_DEFAULT_NAME: PET_DEFAULT_NAME,
    DECO_CATS: DECO_CATS, ITEMS: ITEMS,
    BACKGROUNDS: BACKGROUNDS, EFFECTS: EFFECTS,
    bgById: bgById, effectById: effectById,
    STAR_RULE: STAR_RULE, GRADES: GRADES, GRADE_GROUP: GRADE_GROUP,
    petById: petById, decoById: decoById, titleFor: titleFor,
    catName: catName, slotClash: slotClash, equipDeco: equipDeco, itemById: itemById,
    nextTitle: nextTitle, petSvg: petSvg, decoSvg: decoSvg,
    petTile: petTile, fxMini: fxMini
  };
})();
