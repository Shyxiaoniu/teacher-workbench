/* ============================================================
   打字冒险岛 · 汉字拼音标注表（pinyin.js）
   ------------------------------------------------------------
   用途：中文关卡在「课文区」每个汉字上方标注拼音 + 声调，
        生僻字（磅礴、亵渎、氤氲、瞑目…）学生也能照读照打。

   数据形态：一条 "字+拼音" 的紧凑串，空格分隔，首字符是汉字、
   其余是带声调的拼音。相比 {字:'拼音'} 的对象字面量，体积小一半，
   也便于人工按顺序校对（就是按 Unicode 排序排的）。

   多音字：默认给最常用的读音，另用 WORDS 做「词级覆盖」——
   标注时先按最长词匹配，命中就整词取音，未命中再退到单字。
   ============================================================ */
window.PY = (function () {
  'use strict';

  var RAW =
    '爱ài 安ān 岸àn 按àn 案àn 暗àn 黯àn 傲ào 奥ào 巴bā 拔bá 爸bà 白bái 百bǎi 斑bān 板bǎn 伴bàn 帮bāng ' +
    '傍bàng 磅bàng 包bāo 宝bǎo 报bào 悲bēi 北běi 倍bèi 本běn 笔bǐ 必bì 蔽bì 壁bì 标biāo 表biǎo 别bié 饼bǐng ' +
    '病bìng 驳bó 礴bó 卜bǔ 哺bǔ 不bù 布bù 步bù 猜cāi 才cái 裁cái 采cǎi 菜cài 蚕cán 璨càn 苍cāng 沧cāng ' +
    '藏cáng 操cāo 草cǎo 侧cè 测cè 层céng 察chá 婵chán 铲chǎn 忏chàn 颤chàn 场chǎng 朝zhāo 车chē 沉chén ' +
    '晨chén 称chēng 成chéng 诚chéng 尺chǐ 憧chōng 虫chóng 稠chóu 出chū 除chú 厨chú 锄chú 处chù 船chuán ' +
    '窗chuāng 床chuáng 吹chuī 春chūn 辞cí 慈cí 此cǐ 葱cōng 璀cuǐ 悴cuì 村cūn 寸cùn 挫cuò 答dá 大dà 代dài ' +
    '丹dān 但dàn 当dāng 刀dāo 到dào 稻dào 得dé 德dé 地dì 灯dēng 登dēng 低dī 滴dī 迪dí 笛dí 第dì 典diǎn ' +
    '点diǎn 殿diàn 雕diāo 仃dīng 顶dǐng 鼎dǐng 冬dōng 抖dǒu 豆dòu 独dú 读dú 渎dú 牍dú 笃dǔ 睹dǔ 度dù ' +
    '端duān 段duàn 顿dùn 朵duǒ 堕duò 恶è 而ér 耳ěr 二èr 发fā 帆fān 翻fān 烦fán 繁fán 方fāng 房fáng 飞fēi ' +
    '非fēi 绯fēi 粉fěn 风fēng 枫fēng 逢féng 拂fú 浮fú 咐fù 复fù 赋fù 富fù 覆fù 敢gǎn 干gān 高gāo 告gào ' +
    '哥gē 更gèng 弓gōng 躬gōng 共gòng 古gǔ 谷gǔ 骨gǔ 鼓gǔ 故gù 瓜guā 挂guà 关guān 观guān 光guāng 广guǎng ' +
    '规guī 瑰guī 贵guì 聒guō 国guó 果guǒ 裹guǒ 过guò 还hái 海hǎi 含hán 寒hán 汗hàn 颔hàn 禾hé 何hé 和hé ' +
    '河hé 荷hé 鹤hè 黑hēi 痕hén 烘hōng 红hóng 后hòu 乎hū 呼hū 忽hū 湖hú 户hù 护hù 花huā 化huà 话huà ' +
    '槐huái 欢huān 唤huàn 患huàn 黄huáng 惶huáng 煌huáng 灰huī 晖huī 辉huī 回huí 悔huǐ 汇huì 会huì 慧huì ' +
    '昏hūn 浑hún 活huó 火huǒ 惑huò 鸡jī 吉jí 汲jí 疾jí 棘jí 集jí 辑jí 藉jiè 几jǐ 己jǐ 记jì 济jì 绩jì 佳jiā ' +
    '家jiā 假jiǎ 尖jiān 坚jiān 间jiān 剪jiǎn 见jiàn 涧jiàn 鉴jiàn 箭jiàn 江jiāng 将jiāng 降jiàng 骄jiāo ' +
    '椒jiāo 蕉jiāo 角jiǎo 教jiào 阶jiē 皆jiē 节jié 结jié 捷jié 竭jié 姐jiě 界jiè 今jīn 筋jīn 襟jīn 尽jìn ' +
    '进jìn 劲jìn 晶jīng 睛jīng 精jīng 阱jǐng 憬jǐng 静jìng 境jìng 九jiǔ 久jiǔ 酒jiǔ 居jū 菊jú 举jǔ 句jù ' +
    '拒jù 炬jù 俱jù 据jù 娟juān 决jué 觉jué 绝jué 攫jué 君jūn 峻jùn 开kāi 慨kǎi 看kàn 慷kāng 考kǎo 可kě ' +
    '客kè 课kè 恳kěn 空kōng 恐kǒng 枯kū 苦kǔ 快kuài 矿kuàng 阔kuò 蜡là 辣là 来lái 兰lán 婪lán 蓝lán 览lǎn ' +
    '懒lǎn 狼láng 朗lǎng 浪làng 劳láo 老lǎo 乐lè 雷léi 泪lèi 梨lí 黎lí 里lǐ 理lǐ 力lì 立lì 丽lì 励lì 利lì ' +
    '怜lián 帘lián 莲lián 涟lián 练liàn 两liǎng 亮liàng 辽liáo 嘹liáo 邻lín 林lín 遴lín 伶líng 凌líng ' +
    '留liú 流liú 柳liǔ 龙lóng 茏lóng 笼lóng 楼lóu 陋lòu 录lù 路lù 露lù 乱luàn 论lùn 萝luó 逻luó 落luò ' +
    '律lǜ 绿lǜ 略lüè 妈mā 卖mài 袤mào 眉méi 梅méi 每měi 美měi 门mén 梦mèng 秘mì 密mì 谧mì 蜜mì 眠mián ' +
    '绵mián 苗miáo 庙miào 蔑miè 民mín 悯mǐn 敏mǐn 名míng 明míng 瞑míng 磨mó 漠mò 默mò 木mù 目mù 那nà ' +
    '奶nǎi 南nán 难nán 嫩nèn 泥ní 年nián 酿niàng 鸟niǎo 弄nòng 努nǔ 怕pà 湃pài 盘pán 盼pàn 畔pàn 朋péng ' +
    '澎péng 皮pí 媲pì 飘piāo 婆pó 破pò 葡pú 朴pǔ 谱pǔ 瀑pù 栖qī 其qí 崎qí 企qǐ 启qǐ 契qì 憩qì 千qiān ' +
    '迁qiān 铅qiān 前qián 浅qiǎn 堑qiàn 羌qiāng 墙qiáng 悄qiāo 憔qiáo 峭qiào 茄qié 惬qiè 亲qīn 勤qín ' +
    '青qīng 轻qīng 倾qīng 清qīng 蜻qīng 情qíng 庆qìng 穷qióng 秋qiū 求qiú 球qiú 岖qū 渠qú 取qǔ 去qù ' +
    '群qún 然rán 染rǎn 饶ráo 娆ráo 绕rào 人rén 认rèn 任rèn 日rì 嵘róng 冗rǒng 如rú 入rù 软ruǎn 润rùn ' +
    '塞sāi 三sān 骚sāo 色sè 森sēn 沙shā 山shān 赏shǎng 上shàng 烧shāo 稍shāo 射shè 谁shuí 身shēn 深shēn ' +
    '神shén 生shēng 声shēng 胜shèng 盛shèng 失shī 师shī 诗shī 时shí 识shí 实shí 始shǐ 式shì 似sì 事shì ' +
    '试shì 视shì 是shì 室shì 手shǒu 首shǒu 书shū 舒shū 疏shū 输shū 暑shǔ 树shù 数shù 霜shuāng 水shuǐ ' +
    '说shuō 丝sī 思sī 斯sī 死sǐ 松sōng 擞sǒu 肃sù 素sù 髓suǐ 岁suì 碎suì 邃suì 娑suō 所suǒ 索suǒ 苔tái ' +
    '太tài 贪tān 探tàn 堂táng 陶táo 萄táo 提tí 啼tí 体tǐ 天tiān 田tián 甜tián 停tíng 蜓tíng 挺tǐng 同tóng ' +
    '童tóng 痛tòng 头tóu 图tú 土tǔ 吐tǔ 团tuán 推tuī 托tuō 蛙wā 弯wān 蜿wān 宛wǎn 晚wǎn 万wàn 往wǎng ' +
    '忘wàng 旺wàng 望wàng 危wēi 惟wéi 伟wěi 卫wèi 为wèi 畏wèi 慰wèi 文wén 闻wén 问wèn 翁wēng 我wǒ 无wú ' +
    '吾wú 午wǔ 物wù 夕xī 西xī 希xī 息xī 惜xī 习xí 徙xǐ 喜xǐ 戏xì 细xì 霞xiá 下xià 夏xià 先xiān 现xiàn ' +
    '陷xiàn 乡xiāng 香xiāng 祥xiáng 响xiǎng 想xiǎng 橡xiàng 消xiāo 嚣xiāo 小xiǎo 晓xiǎo 校xiào 笑xiào ' +
    '谐xié 写xiě 亵xiè 心xīn 辛xīn 欣xīn 馨xīn 信xìn 星xīng 行xíng 形xíng 杏xìng 雄xióng 熊xióng 休xiū ' +
    '须xū 许xǔ 宣xuān 喧xuān 悬xuán 选xuǎn 学xué 雪xuě 谑xuè 熏xūn 逊xùn 崖yá 雅yǎ 延yán 严yán 言yán ' +
    '蜒yán 眼yǎn 验yàn 扬yáng 阳yáng 杨yáng 仰yǎng 妖yāo 摇yáo 遥yáo 杳yǎo 要yào 爷yé 也yě 野yě 业yè ' +
    '叶yè 曳yè 夜yè 一yī 依yī 疑yí 已yǐ 以yǐ 矣yǐ 忆yì 亦yì 异yì 抑yì 意yì 毅yì 氤yīn 殷yīn 银yín 引yǐn ' +
    '隐yǐn 应yìng 莹yíng 萦yíng 勇yǒng 优yōu 忧yōu 幽yōu 犹yóu 油yóu 友yǒu 有yǒu 又yòu 诱yòu 迂yū 淤yū ' +
    '于yú 余yú 鱼yú 予yǔ 宇yǔ 雨yǔ 语yǔ 玉yù 育yù 预yù 欲yù 豫yù 园yuán 垣yuán 原yuán 缘yuán 猿yuán ' +
    '源yuán 远yuǎn 怨yuàn 愿yuàn 约yuē 月yuè 晕yùn 氲yūn 云yún 匀yún 酝yùn 蕴yùn 在zài 早zǎo 噪zào 躁zào ' +
    '则zé 渣zhā 瞻zhān 展zhǎn 长cháng 掌zhǎng 招zhāo 照zhào 遮zhē 褶zhě 着zhe 珍zhēn 真zhēn 峥zhēng ' +
    '筝zhēng 正zhèng 证zhèng 之zhī 枝zhī 知zhī 直zhí 只zhǐ 纸zhǐ 志zhì 挚zhì 致zhì 智zhì 置zhì 中zhōng ' +
    '终zhōng 众zhòng 重zhòng 舟zhōu 洲zhōu 宙zhòu 皱zhòu 竹zhú 嘱zhǔ 助zhù 住zhù 转zhuǎn 庄zhuāng ' +
    '装zhuāng 壮zhuàng 缀zhuì 濯zhuó 资zī 子zǐ 滓zǐ 自zì 字zì 粽zòng 嘴zuǐ 最zuì 醉zuì 尊zūn 琢zhuó ' +
    '作zuò 坐zuò ' +
    /* —— 《从百草园到三味书屋》新增（2026-11-02） —— */
    '的de 面miàn 个gè 很hěn 相xiāng 传chuán 叫jiào 并bìng 屋wū 起qǐ 给gěi 朱zhū 公gōng 孙sūn 了le 连lián 末mò 次cì 经jīng 隔gé 七qī 八bā 确què 凿záo 些xiē 却què ' +
    '碧bì 畦qí 滑huá 石shí 井jǐng 栏lán 皂zào 荚jiá 紫zǐ 桑sāng 椹shèn 鸣míng 蝉chán 吟yín 肥féi 胖pàng 蜂fēng 伏fú 雀què 从cóng 窜cuàn 向xiàng 霄xiāo 单dān 周zhōu 围wéi 短duǎn 根gēn 带dài 就jiù 限xiàn 趣qù 味wèi ' +
    '蛉líng 这zhè 唱chàng 蟋xī 蟀shuài 们men 弹tán 琴qín 断duàn 砖zhuān 遇yù 蜈wú 蚣gōng 蝥máo 倘tǎng 若ruò 用yòng 指zhǐ 它tā 脊jǐ 梁liáng 便biàn 啪pā 窍qiào 喷pēn 阵zhèn 烟yān 雾wù 乌wū 藤téng 缠chán 络luò 般bān 臃yōng 肿zhǒng ' +
    '像xiàng 吃chī 仙xiān 常cháng 牵qiān 曾céng 因yīn 坏huài 没méi 块kuài 样yàng 刺cì 摘zhāi 盆pén 珊shān 瑚hú 珠zhū 攒zǎn 酸suān 都dōu 比bǐ 好hǎo 条tiáo 赤chì 蛇shé ' +
    '讲jiǎng 听tīng 功gōng 院yuàn 纳nà 凉liáng 候hòu 突tū 他tā 四sì 女nǚ 脸liǎn 兴xīng 竟jìng 走zǒu 谈tán 尚shàng 机jī 气qì 定dìng 怪guài 能néng 肉ròu 吓xià 道dào 妨fáng 盒hé 放fàng 枕zhěn 边biān 卧wò 虽suī 办bàn 总zǒng 睡shuì 半bàn 外wài 豁huō 金jīn 什shén 么me 音yīn 敛liǎn ' +
    '呢ne 吸xī 脑nǎo 被bèi 治zhì 训xùn 陌mò 你nǐ 使shǐ 做zuò 险xiǎn 乘chéng 担dān 且qiě 极jí 丛cóng 旁páng 较jiào 拍pāi 全quán 印yìn 塑sù 罗luó 汉hàn 需xū 荒huāng 迹jì 罕hǎn 至zhì 宜yí 捕bǔ 薄báo 积jī 盖gài 觅mì 食shí 扫sǎo ' +
    '棒bàng 支zhī 筛shāi 撒sǎ 秕bǐ 系xì 绳shéng 啄zhuó 底dǐ 拉lā 罩zhào 麻má 多duō 颊jiá 张zhāng 性xìng 养yǎng 闰rùn 父fù 授shòu 法fǎ 跑pǎo 费fèi 捉zhuō 获huò 十shí 叉chā 袋dài 撞zhuàng 由yóu 急jí 及jí 等děng 送sòng 塾shú 城chéng 厉lì 毁huǐ ' +
    '吧ba 抛pāo 罢bà 站zhàn 跳tiào 东dōng 桥qiáo 扇shàn 匾biǎn 幅fú 画huà 鹿lù 孔kǒng 牌pái 位wèi 对duì 礼lǐ 算suàn 拜bài 蔼ǎi 瘦shòu 戴dài 镜jìng 恭gōng 敬jìng 质zhì 博bó 哪nǎ 朔shuò 渊yuān 种zhǒng 曰yuē 哉zāi 冤yuān 浇jiāo 释shì 详xiáng 阿ā 她tā 毕bì 怎zěn 退tuì 赶gǎn 忙máng 怒nù 该gāi 宿sù 儒rú 谓wèi 者zhě 乃nǎi 纪jì 初chū 渐jiàn 加jiā 五wǔ 爬pá 坛tán 折zhé 或huò 桂guì 寻xún 蜕tuì ' +
    '工gōng 蝇yíng 喂wèi 蚂mǎ 蚁yǐ 陆lù 续xù 戒jiè 罚fá 跪guì 普pǔ 通tōng 瞪dèng 喉hóu 咙lóng 沸fèi 念niàn 仁rén 齿chǐ 缺quē 狗gǒu 窦dòu 潜qián 勿wù 厥jué 错cuò 贡gòng 苞bāo 茅máo 橘jú 柚yòu 铁tiě 挥huī 倜tì 傥tǎng 惊jīng 叵pǒ 颠diān 倒dǎo 淋lín 漓lí 噫yī 杯bēi 未wèi 嗬hē 章zhāng 微wēi 拗ǎo ' +
    '糊hú 盔kuī 甲jiǎ 套tào 儿ér 荆jīng 川chuān 蒙méng 绣xiù 描miáo 影yǐng 少shǎo 片piàn 荡dàng 寇kòu 游yóu 钱qián 锡xī 箔bó 店diàn 主zhǔ 升shēng 绅shēn 士shì ' +
    /* —— 分类文章库新增（2026-11-02）：故事 / 写景 / 科普 / 诗歌 / 古文 / 古诗 —— */
    '散sàn 母mǔ 妻qī 累lèi 服fú 拿ná 迟chí 熬áo 新xīn 随suí 铺pū 浓nóng 淡dàn 芽yá 咕gū 泡pào 切qiē 命mìng 伙huǒ 分fēn 歧qí 平píng 顺shùn 惯guàn 强qiáng 霎shà 感gǎn 责zé 找zhǎo 拆chāi 各gè 委wěi 屈qū 摸mō 变biàn 整zhěng 齐qí 口kǒu 波bō 粼lín 塘táng 背bèi 蹲dūn 慢màn 稳wěn 仔zǐ 世shì 与yǔ 祖zǔ 差chāi 交jiāo 卸xiè 祸huò 京jīng 徐xú 州zhōu 打dǎ 跟gēn 奔bēn 丧sāng 满mǎn 禁jīn 簌sù 亏kuī 借jiè 景jǐng 惨cǎn 闲xián 完wán 谋móu 买mǎi 动dòng 台tái 栅zhà 顾gù 穿chuān 肯kěn 让ràng ' +
    '帽mào 马mǎ 褂guà 棉mián 袍páo 蹒pán 跚shān 容róng 易yì 攀pān 脚jiǎo 再zài 缩suō 左zuǒ 显xiǎn 紧jǐn 拭shì 膀bǎng 疼téng 害hài 箸zhù 诸zhū 期qī 唉āi 蚊wén 狮shī 量liàng 爪zhuǎ 抓zhuā 牙yá 咬yǎo 吗ma 男nán 架jià 喇lǎ 叭bā 冲chōng 专zhuān 鼻bí 毛máo 把bǎ 战zhàn 凯kǎi 歌gē 蜘zhī 蛛zhū 网wǎng 粘zhān 掉diào 叹tàn 料liào 灭miè 适shì 败bài 近jìn 刚gāng ' +
    '醒xǐng 涨zhǎng 偷tōu 钻zuān 瞧qiáo 躺tǎng 滚gǔn 踢tī 赛sài 趟tàng 迷mí 桃táo 闭bì 仿fǎng 佛fú 嗡wēng 闹nào 蝴hú 蝶dié 遍biàn 杂zá 眨zhǎ 呀ya 抚fǔ 混hùn 湿shī 巢cháo 脆cuì 曲qǔ 牛niú 牧mù 恼nǎo 针zhēn 斜xié 织zhī 逼bī 撑chēng 伞sǎn 农nóng 披pī 蓑suō 笠lì 稀xī 孩hái 份fèn 计jì 夫fū 娃wá 姑gū 娘niáng ' +
    '健jiàn 胳gē 膊bo 腰yāo 领lǐng 刮guā 奇qí 伦lún 敦dūn 晴qíng 热rè 永yǒng 毒dú 反fǎn 温wēn 设shè 请qǐng 晒shài 暖nuǎn 圈quān 特tè 篮lán 准zhǔn 保bǎo 靠kào 妙miào 矮ǎi 越yuè 髻jì 妇fù 镶xiāng 坡pō 厚hòu 件jiàn 纹wén 衣yī 肌jī 肤fū 羞xiū 受shòu 秀xiù 苏sū 冰bīng 融róng 萌méng 燕yàn 翩piān 归guī 炎yán 季jì 植zhí 孕yùn 熟shú 雁yàn 跃yuè 际jì ' +
    '昆kūn 销xiāo 匿nì 呈chéng 衰shuāi 象xiàng 备bèi 迎yíng 接jiē 载zài 途tú 亚yà 区qū 域yù 注zhù 荣róng 排pái 耕gēng 示shì 懂dǒng 割gē 麦mài 插chā 莺yīng 谚yàn 丰fēng 研yán 究jiū 产chǎn 科kē 电diàn 键jiàn 六liù 屏píng 幕mù 编biān 号hào 码mǎ 队duì 固gù 局jú 换huàn 符fú 互hù 制zhì 简jiǎn 僻pì 族zú 收shōu 另lìng 拼pīn 敲qiāo ' +
    '词cí 库kù 列liè 挑tiāo 聪cōng 毫háo 街jiē 闪shǎn 缥piāo 缈miǎo 市shì 陈chén 品pǐn 甚shèn 宽kuān 郎láng 够gòu 骑qí 刻kè 妄wàng 弃qì 叠dié 卷juǎn 沾zhān 仍réng 讶yà 哀āi 灵líng 鸿hóng 丁dīng 调tiáo 阅yuè 葛gě 庐lú 蜀shǔ 亭tíng 蕃fán 晋jìn 李lǐ 唐táng 牡mǔ 蔓màn 益yì 净jìng 玩wán 焉yān 逸yì 鲜xiǎn ' +
    '峡xiá 阙quē 岩yán 嶂zhàng 曦xī 襄xiāng 陵líng 沿yán 溯sù 阻zǔ 王wáng 帝dì 暮mù 御yù 湍tuān 潭tán 巘yǎn 柏bǎi 泉quán 漱shù 茂mào 良liáng 旦dàn 啸xiào 属zhǔ 凄qī 渔yú 巫wū 裳cháng 唧jī 杼zhù 昨zuó 军jūn 帖tiě 兵bīng 兄xiōng 鞍ān 替tì 征zhēng 骏jùn 鞯jiān 辔pèi 鞭biān 溅jiān 胡hú 啾jiū 赴fù 戎róng 柝tuò ' +
    '策cè 勋xūn 赐cì 驰chí 足zú 郭guō 扶fú 姊zǐ 妹mèi 妆zhuāng 弟dì 霍huò 猪zhū 羊yáng 阁gé 脱tuō 著zhuó 旧jiù 鬓bìn 兔tù 扑pū 雌cí 离lí 双shuāng 辨biàn 炭tàn 伐fá 薪xīn 尘chén 营yíng 贱jiàn 驾jià 辗zhǎn 辙zhé 困kùn 饥jī 歇xiē 衫shān 敕chì 叱chì 斤jīn 宫gōng 驱qū 匹pǐ 纱shā 丈zhàng 绫líng 充chōng ' +
    /* —— 2026-11-03 扩容至每类 20 篇：新增生字（pypinyin 按上下文标注 + 人工校订） —— */
    '丘qiū 丞chéng 丢diū 丫yā 串chuàn 临lín 乏fá 乖guāi ' +
    '乳rǔ 争zhēng 亍chù 亡wáng 亩mǔ 仓cāng 仗zhàng 仞rèn ' +
    '令lìng 价jià 伤shāng 伸shēn 伺sì 伽qié 佁yǐ 例lì ' +
    '侍shì 侦zhēn 促cù 俄é 俨yǎn 俩liǎ 俭jiǎn 修xiū ' +
    '俯fǔ 俶chù 倌guān 倔jué 倚yǐ 倦juàn 倭wō 值zhí ' +
    '偏piān 偶ǒu 储chǔ 催cuī 僧sēng 僵jiāng 兀wù 允yǔn ' +
    '元yuán 免miǎn 具jù 兽shòu 冀jì 内nèi 冒mào 冠guàn ' +
    '冷lěng 冻dòng 冽liè 凝níng 凡fán 凤fèng 凰huáng 刍chú ' +
    '划huà 创chuàng 判pàn 刷shuā 刹shā 剧jù 剩shèng 副fù ' +
    '劈pī 劝quàn 务wù 势shì 勃bó 匆cōng 匮kuì 占zhàn ' +
    '即jí 卵luǎn 卿qīng 厂chǎng 厅tīng 历lì 压yā 厦shà ' +
    '参cān 叟sǒu 叩kòu 叮dīng 右yòu 叼diāo 合hé 吏lì ' +
    '吕lǚ 吞tūn 吠fèi 否fǒu 吨dūn 吩fēn 吱zhī 吴wú ' +
    '吼hǒu 呐nà 员yuán 呜wū 呵hē 咏yǒng 咛níng 咱zán ' +
    '咸xián 咽yè 哇wa 哗huā 哨shào 哼hēng 唇chún 唬hǔ ' +
    '唰shuā 商shāng 啊a 啦la 善shàn 喊hǎn 喝hē 喻yù ' +
    '嗟jiē 嘀dí 嘘xū 嘟dū 嘤yīng 嘶sī 器qì 噼pī ' +
    '嚷rǎng 圃pǔ 圆yuán 圾jī 址zhǐ 均jūn 坠zhuì 坳ào ' +
    '坻chí 垂chuí 垃lā 垦kěn 埂gěng 埋mái 埠bù 基jī ' +
    '堆duī 堡bǎo 堤dī 堵dǔ 塌tā 塔tǎ 增zēng 墨mò ' +
    '壑hè 壕háo 壤rǎng 壳ké 壶hú 夷yí 夸kuā 夹jiā ' +
    '夺duó 奈nài 奉fèng 奏zòu 奖jiǎng 妒dù 妪yù 姆mǔ ' +
    '姓xìng 姨yí 姿zī 威wēi 娇jiāo 娜nuó 娲wā 婀ē ' +
    '婚hūn 媚mèi 嫁jià 嫂sǎo 孀shuāng 存cún 孤gū 孰shú ' +
    '宁níng 宅zhái 守shǒu 宋sòng 宗zōng 官guān 寂jì 寄jì ' +
    '寐mèi 寓yù 寝qǐn 寞mò 寡guǎ 寥liáo 寨zhài 寺sì ' +
    '导dǎo 寿shòu 封fēng 尔ěr 尝cháng 尤yóu 尻kāo 尾wěi ' +
    '屠tú 屹yì 屿yǔ 岂qǐ 岛dǎo 岭lǐng 岱dài 岳yuè ' +
    '峙zhì 峦luán 峰fēng 崩bēng 嵁kān 巨jù 巷xiàng 巾jīn ' +
    '幔màn 幸xìng 幻huàn 幼yòu 庇bì 序xù 废fèi 座zuò ' +
    '庭tíng 康kāng 廊láng 廓kuò 建jiàn 弛chí 弥mí 弩nǔ ' +
    '弱ruò 彩cǎi 彳chì 彷páng 役yì 彼bǐ 径jìng 待dài ' +
    '徨huáng 循xún 忌jì 忍rěn 忠zhōng 忿fèn 怀huái 态tài ' +
    '怅chàng 怆chuàng 怡yí 恋liàn 恒héng 恕shù 恢huī 恨hèn ' +
    '恩ēn 悉xī 悚sǒng 悟wù 悠yōu 您nín 惆chóu 惋wǎn ' +
    '惠huì 惧jù 惩chéng 惹rě 愁chóu 愉yú 愚yú 愠yùn ' +
    '慌huāng 慎shèn 慕mù 憋biē 懦nuò 戈gē 戍shù 截jié ' +
    '戾lì 扁biǎn 扎zhā 扩kuò 扭niǔ 承chéng 投tóu 抗kàng ' +
    '抢qiǎng 抬tái 抱bào 抵dǐ 抹mǒ 抽chōu 拄zhǔ 拖tuō ' +
    '拟nǐ 拢lǒng 拦lán 拨bō 择zé 括kuò 拱gǒng 拾shí ' +
    '持chí 挖wā 挠náo 挡dǎng 挤jǐ 挨āi 振zhèn 挹yì ' +
    '挽wǎn 捏niē 损sǔn 捡jiǎn 捣dǎo 捶chuí 掀xiān 掘jué ' +
    '掠lüè 掩yǎn 掺chān 揉róu 揣chuāi 揭jiē 搁gē 搅jiǎo ' +
    '搔sāo 搬bān 搭dā 携xié 摆bǎi 摔shuāi 摞luò 播bō ' +
    '擂léi 擦cā 改gǎi 攻gōng 敌dí 救jiù 斓lán 斥chì ' +
    '旅lǚ 旋xuán 旗qí 既jì 旷kuàng 映yìng 晃huàng 晦huì ' +
    '暇xiá 暝míng 暴bào 曹cáo 朦méng 杀shā 权quán 杆gān ' +
    '材cái 杓biāo 杖zhàng 杞qǐ 束shù 杭háng 构gòu 枚méi ' +
    '枣zǎo 柄bǐng 某mǒu 柔róu 柘zhè 查chá 柱zhù 栀zhī ' +
    '栎lì 株zhū 核hé 格gé 栽zāi 桉ān 桌zhuō 桐tóng ' +
    '桕jiù 桦huà 桨jiǎng 桩zhuāng 梢shāo 梳shū 梵fàn 检jiǎn ' +
    '棋qí 棕zōng 棚péng 棵kē 椅yǐ 椰yē 楚chǔ 概gài ' +
    '榄lǎn 榛zhēn 榨zhà 榴liú 模mú 横héng 樱yīng 橄gǎn ' +
    '橙chéng 橹lǔ 欧ōu 欺qī 止zhǐ 武wǔ 殆dài 残cán ' +
    '毙bì 毡zhān 氏shì 汀tīng 汁zhī 汝rǔ 池chí 汤tāng ' +
    '汽qì 沃wò 沈shěn 沥lì 泊bó 泛fàn 泠líng 泣qì ' +
    '泰tài 泳yǒng 泻xiè 泼pō 洁jié 洋yáng 洒sǎ 洗xǐ ' +
    '洛luò 洞dòng 洪hóng 洼wā 派pài 浆jiāng 浩hào 浴yù ' +
    '浸jìn 涂tú 涉shè 涌yǒng 涛tāo 涩sè 涯yá 淙cóng ' +
    '淫yín 淮huái 淹yān 添tiān 渡dù 渤bó 渺miǎo 湣mǐn ' +
    '溉gài 溜liū 溪xī 滁chú 滋zī 滥làn 滨bīn 滩tān ' +
    '漂piāo 漆qī 漉lù 漏lòu 漫màn 漾yàng 潮cháo 潺chán ' +
    '澄chéng 澈chè 澜lán 澹dàn 激jī 瀚hàn 灌guàn 灿càn ' +
    '炊chuī 炸zhà 烁shuò 烂làn 烈liè 烛zhú 烫tàng 烽fēng ' +
    '焦jiāo 煤méi 煲bāo 熳màn 燃rán 燥zào 爆bào 爹diē ' +
    '爽shuǎng 牢láo 牲shēng 牺xī 犬quǎn 犯fàn 状zhuàng 犷guǎng ' +
    '狐hú 狭xiá 狸lí 猎liè 猛měng 猫māo 献xiàn 率lǜ ' +
    '环huán 玲líng 玻bō 班bān 珮pèi 琅láng 琊yá 琐suǒ ' +
    '瑕xiá 瑜yú 瑟sè 璃lí 璧bì 瓣bàn 瓦wǎ 甘gān ' +
    '甩shuǎi 甸diàn 畚běn 番fān 疆jiāng 疲pí 痪huàn 瘫tān ' +
    '皇huáng 皓hào 盈yíng 盏zhǎn 盗dào 盲máng 盾dùn 省shěng ' +
    '眈dān 眦zì 睁zhēng 督dū 睨nì 睫jié 瞥piē 瞬shùn ' +
    '瞿qú 矛máo 矜jīn 矢shǐ 砍kǎn 砸zá 硬yìng 碍ài ' +
    '碗wǎn 碣jié 碰pèng 磁cí 磕kē 磬qìng 社shè 禽qín ' +
    '私sī 秃tū 秒miǎo 秤chèng 秦qín 移yí 程chéng 稼jià ' +
    '穗suì 穴xué 窄zhǎi 窘jiǒng 窟kū 窥kuī 窿lóng 竖shù ' +
    '竞jìng 竦sǒng 竽yú 竿gān 笋sǔn 笨bèn 筏fá 筒tǒng ' +
    '签qiān 箕jī 管guǎn 箫xiāo 篁huáng 篙gāo 篱lí 簇cù ' +
    '簪zān 米mǐ 类lèi 粒lì 粗cū 粟sù 粥zhōu 粪fèn ' +
    '糖táng 糙cāo 絮xù 纤xiān 级jí 纶lún 纷fēn 线xiàn ' +
    '绊bàn 绘huì 继jì 绮qǐ 维wéi 综zōng 绽zhàn 缆lǎn ' +
    '缓huǎn 缕lǚ 缝fèng 缤bīn 缨yīng 缬xié 罔wǎng 罥juàn ' +
    '罪zuì 翅chì 翎líng 翔xiáng 翕xī 翠cuì 翡fěi 翼yì ' +
    '耀yào 耍shuǎ 耐nài 耗hào 耶yé 耸sǒng 聊liáo 职zhí ' +
    '联lián 聚jù 肠cháng 股gǔ 胞bāo 胧lóng 胶jiāo 胸xiōng ' +
    '脖bó 脾pí 腊là 腔qiāng 腾téng 腿tuǐ 膑bìn 膜mó ' +
    '膝xī 臂bei 臣chén 舌shé 舍shě 舞wǔ 航háng 舰jiàn ' +
    '舱cāng 舷xián 艳yàn 芦lú 芬fēn 芳fāng 芷zhǐ 苇wěi ' +
    '苒rǎn 苫shān 英yīng 苹píng 茫máng 茶chá 荇xìng 荏rěn ' +
    '荫yīn 药yào 莓méi 莫mò 莽mǎng 菇gū 萍píng 萎wěi ' +
    '萧xiāo 葫hú 蒲pú 蒸zhēng 蓬péng 蔚wèi 蕊ruǐ 藻zǎo ' +
    '蘑mó 虎hǔ 虐nüè 虑lǜ 虚xū 虹hóng 虾xiā 蚪dǒu ' +
    '蛋dàn 蛮mán 蛳sī 蛾é 蜗wō 蜷quán 蝌kē 蝗huáng ' +
    '蝙biān 蝠fú 螃páng 螺luó 蟹xiè 蠕rú 蠢chǔn 血xuè ' +
    '衔xián 衡héng 补bǔ 衬chèn 衾qīn 袅niǎo 袖xiù 袭xí ' +
    '裂liè 裙qún 褪tuì 解jiě 触chù 警jǐng 讨tǎo 议yì ' +
    '讯xùn 访fǎng 诈zhà 诉sù 译yì 诧chà 误wù 谅liàng ' +
    '谎huǎng 谢xiè 谨jǐn 豚tún 貂diāo 貌mào 贝bèi 负fù ' +
    '贯guàn 贴tiē 贼zéi 贾jiǎ 赞zàn 赠zèng 赢yíng 赫hè ' +
    '赵zhào 超chāo 趴pā 跐cī 距jù 跷qiāo 踊yǒng 踏tà ' +
    '踩cǎi 踪zōng 踮diǎn 蹄tí 蹈dǎo 蹊qī 蹦bèng 躇chú ' +
    '躲duǒ 轧yà 轩xuān 轮lún 辄zhé 辆liàng 辈bèi 辟pì ' +
    '辩biàn 辫biàn 达dá 运yùn 返fǎn 违wéi 迫pò 述shù ' +
    '迸bèng 追zhuī 逃táo 透tòu 逐zhú 递dì 逗dòu 逝shì ' +
    '速sù 造zào 逾yú 遂suì 遗yí 遣qiǎn 遭zāo 避bì ' +
    '邀yāo 邈miǎo 邑yì 邪xié 邮yóu 邺yè 郁yù 郊jiāo ' +
    '郑zhèng 部bù 酌zhuó 钓diào 钟zhōng 钩gōu 铃líng 铛dāng ' +
    '锅guō 锋fēng 锦jǐn 镀dù 镇zhèn 闷mèn 阡qiān 防fáng ' +
    '阴yīn 附fù 陪péi 隆lóng 障zhàng 隧suì 雳lì 零líng ' +
    '雹báo 震zhèn 霏fēi 霸bà 霹pī 靛diàn 靡mǐ 革gé ' +
    '鞋xié 韵yùn 页yè 顷qǐng 颂sòng 颇pǒ 颈jǐng 颐yí ' +
    '颓tuí 颗kē 题tí 颜yán 饭fàn 饮yǐn 饱bǎo 饿è ' +
    '馆guǎn 驼tuó 骂mà 骆luò 骗piàn 骤zhòu 髁kē 髫tiáo ' +
    '鬼guǐ 魂hún 魏wèi 鲁lǔ 鲛jiāo 鳖biē 鳞lín 鸢yuān ' +
    '鸥ōu 鸦yā 鸭yā 鹅é 鹊què 鹰yīng 麒qí 麟lín ' +
    '黏nián 黠xiá 鼠shǔ 龀chèn';

  /* 多音字 · 词级覆盖：先按最长词匹配，命中的整个词取这里定的音。
     只列「默认读音会读错」的词，其余走单字表。 */
  var WORDS = {
    '数风流人物': 'shǔ fēng liú rén wù',
    '萝卜': 'luó bo',
    '匀称': 'yún chèn',
    '慰藉': 'wèi jiè',
    '狼藉': 'láng jí',
    '语塞': 'yǔ sè',
    '应和': 'yìng hè',
    '朝辞': 'zhāo cí',
    '着落': 'zhuó luò',
    /* 《从百草园到三味书屋》里的多音字词 */
    '确凿': 'què záo',
    '高兴': 'gāo xìng',
    '攒成': 'cuán chéng',
    '系一': 'jì yī',
    /* —— 分类文章库里的多音字词（2026-11-02） ——
       只收「按单字默认读音会读错」的：差使不是 chà、不禁不是 jìn、
       胡骑不是 qí、旧时裳不是 shang。 */
    '差使': 'chāi shǐ',
    '不禁': 'bù jīn',
    '一切': 'yī qiè',
    '仿佛': 'fǎng fú',
    '薄烟': 'bó yān',
    '薄薄的': 'báo báo de',
    '薄雪': 'báo xuě',
    '奔丧': 'bēn sāng',
    '丧事': 'sāng shì',
    '栅栏': 'zhà lán',
    '马褂': 'mǎ guà',
    '蹒跚': 'pán shān',
    '举箸': 'jǔ zhù',
    '爪子': 'zhuǎ zi',
    '看护妇': 'kān hù fù',
    '髻儿': 'jì ér',
    '圈儿': 'quān ér',
    '愿为': 'yuàn wèi',
    '扶将': 'fú jiāng',
    '胡骑': 'hú jì',
    '两骑': 'liǎng jì',
    '燕山': 'yān shān',
    '军帖': 'jūn tiě',
    '可汗': 'kè hán',
    '十二转': 'shí èr zhuǎn',
    '旧时裳': 'jiù shí cháng',
    '溅溅': 'jiān jiān',
    '啾啾': 'jiū jiū',
    '金柝': 'jīn tuò',
    '策勋': 'cè xūn',
    '红妆': 'hóng zhuāng',
    '云鬓': 'yún bìn',
    '傍地': 'bàng dì',
    '扑朔': 'pū shuò',
    '迷离': 'mí lí',
    '火伴': 'huǒ bàn',
    '属引': 'zhǔ yǐn',
    '哀转': 'āi zhuǎn',
    '绝巘': 'jué yǎn',
    '素湍': 'sù tuān',
    '飞漱': 'fēi shù',
    '曦月': 'xī yuè',
    '襄陵': 'xiāng líng',
    '沿溯': 'yán sù',
    '阙处': 'quē chù',
    '甚蕃': 'shèn fán',
    '鲜有闻': 'xiǎn yǒu wén',
    '调素琴': 'tiáo sù qín',
    '诸葛庐': 'zhū gě lú',
    '子云亭': 'zǐ yún tíng',
    '德馨': 'dé xīn',
    '鸿儒': 'hóng rú',
    '白丁': 'bái dīng',
    '叱牛': 'chì niú',
    '驱将': 'qū jiāng',
    '伐薪': 'fá xīn',
    '卖炭翁': 'mài tàn wēng',
    '翩然': 'piān rán',
    '载途': 'zài tú',
    '混着': 'hùn zhe',
    /* —— 2026-11-03 扩容至每类 20 篇：新增多音字词校订 —— */
    '衣裳': 'yī shang',
    '似的': 'shì de',
    '参差': 'cēn cī',
    '伽蓝': 'qié lán',
    '模胡': 'mó hu',
    '倒影': 'dào yǐng',
    '笼罩': 'lǒng zhào',
    '薄雾': 'bó wù',
    '扇动': 'shān dòng',
    '浩浩汤汤': 'hào hào shāng shāng',
    '三重茅': 'sān chóng máo',
    '挂罥': 'guà juàn',
    '悄怆幽邃': 'qiǎo chuàng yōu suì',
    '不亦说乎': 'bù yì yuè hū',
    '三省吾身': 'sān xǐng wú shēn',
    '好之者': 'hào zhī zhě',
    '见往事': 'xiàn wǎng shì',
    '博士邪': 'bó shì yé',
    '曾不能': 'zēng bù néng',
    '曾益': 'zēng yì',
    '生曾云': 'shēng céng yún',
    '荷担': 'hè dàn',
    '之塞': 'zhī sè',
    '身亡所寄': 'wú shēn suǒ jì',
    '亡处亡气': 'wú chù wú qì',
    '亡处亡块': 'wú chù wú kuài',
    '亡以应': 'wú yǐ yìng',
    '舍然大喜': 'shì rán dà xǐ',
    '便要还家': 'biàn yāo huán jiā',
    '人语云': 'rén yù yún',
    '捕鱼为业': 'bǔ yú wéi yè',
    '自以为': 'zì yǐ wéi',
    '以为': 'yǐ wéi',
    '为外人道': 'wéi wài rén dào',
    '率妻子': 'shuài qī zǐ',
    '以杓': 'yǐ sháo',
    '属国': 'shǔ guó',
    '千转': 'qiān zhuàn',
    '即更': 'jí gēng',
    '候骑': 'hòu jì',
    '燕然': 'yān rán',
    '汉塞': 'hàn sài'
  };

  var D = {};
  RAW.split(/\s+/).forEach(function (t) {
    if (!t) return;
    var ch = t.charAt(0);
    var py = t.slice(1);
    if (ch && py) D[ch] = py;
  });

  var WKEYS = Object.keys(WORDS).sort(function (a, b) { return b.length - a.length; });

  /** 单字读音；表里没有返回 ''（标点、数字、英文都走这里） */
  function of(ch) { return D[ch] || ''; }

  /** 把一个字符串拆成 [{c:汉字, p:拼音}]，标点/空格的 p 为 ''。
      词级覆盖优先，命中后整词取音，保证「数风流人物」不会读成 shù。 */
  function annotate(text) {
    text = String(text == null ? '' : text);
    var out = [];
    var i = 0;
    while (i < text.length) {
      var hit = null, hitLen = 0;
      for (var k = 0; k < WKEYS.length; k++) {
        var w = WKEYS[k];
        if (w.length > hitLen && text.substr(i, w.length) === w) { hit = WORDS[w].split(' '); hitLen = w.length; }
      }
      if (hit) {
        for (var j = 0; j < hitLen; j++) out.push({ c: text.charAt(i + j), p: hit[j] || '' });
        i += hitLen;
        continue;
      }
      var c = text.charAt(i);
      out.push({ c: c, p: of(c) });
      i++;
    }
    return out;
  }

  /** 覆盖率自检：返回 {total, missing:[字…]}，教师端/开发时可查漏 */
  function coverage(text) {
    var miss = [], seen = {}, total = 0;
    annotate(text).forEach(function (it) {
      if (!it.p && /[\u4e00-\u9fff]/.test(it.c)) { total++; if (!seen[it.c]) { seen[it.c] = 1; miss.push(it.c); } }
    });
    return { total: total, missing: miss };
  }

  return { dict: D, of: of, annotate: annotate, coverage: coverage, count: Object.keys(D).length };
})();
