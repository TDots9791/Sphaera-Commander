"""Локализация интерфейса: русский (по умолчанию), английский, китайский.

Строки-ключи — русские; tr() отдаёт перевод для текущего языка. Отсутствие
перевода не «пустит пустоту» — вернётся русский ключ. Язык выбирается в
меню «Вид → Язык / Language / 语言» и применяется при следующем запуске
(config: view/language). Полнота словаря для en/zh проверяется тестами.
"""

from __future__ import annotations

LANG = "ru"  # "ru" | "en" | "zh" — выставляется из config при старте приложения
LANGUAGES = (("ru", "Русский"), ("en", "English"), ("zh", "中文"))

# {ключ (русский): (english, 中文)} — генерируется в _STRINGS ниже.
_T = {
    "\n\n[… показаны первые {mib} МиБ файла — остальное скрыто …]":
        ("\n\n[… first {mib} MiB of the file shown — the rest is hidden …]",
         "\n\n[… 仅显示文件的前 {mib} MiB — 其余内容已隐藏 …]"),
    "\n[… hex-обзор ограничен 2 МиБ …]":
        ("\n[… hex view limited to 2 MiB …]", "\n[… 十六进制查看限制为 2 MiB …]"),
    "\nОперация прервана пользователем.":
        ("\nOperation cancelled by user.", "\n操作已被用户中断。"),
    "\n… и ещё {n}": ("\n… and {n} more", "\n… 还有 {n} 项"),
    "   •   отмечено: {count} ({size})":
        ("   •   marked: {count} ({size})", "   •   已标记：{count}（{size}）"),
    " (обрезано)": (" (truncated)", "（已截断）"),
    " • F4 — правка": (" • F4 — edit", " • F4 — 编辑"),
    " • JSON: OK": (" • JSON: OK", " • JSON：正常"),
    " • JSON: ошибка ({err})": (" • JSON: error ({err})", " • JSON：错误（{err}）"),
    " • глава 1 из {count} (F3 — по главам)":
        (" • chapter 1 of {count} (F3 — by chapters)", " • 第 1 章，共 {count} 章（F3 — 按章浏览）"),
    " • ошибок: {n}": (" • errors: {n}", " • 错误：{n}"),
    " • прервано": (" • cancelled", " • 已中断"),
    " … и ещё {n}": (" … and {n} more", " … 还有 {n} 项"),
    "$ {cmd}\nкод выхода: {code}": ("$ {cmd}\nexit code: {code}", "$ {cmd}\n退出代码：{code}"),
    "$ {cmd} — готово": ("$ {cmd} — done", "$ {cmd} — 完成"),
    "&Вид": ("&View", "查看(&V)"),
    "&Панели": ("&Panels", "面板(&P)"),
    "&Справка": ("&Help", "帮助(&H)"),
    "&Файл": ("&File", "文件(&F)"),
    "→ {a}   ← {b}   = {c}   ≠ {d}":
        ("→ {a}   ← {b}   = {c}   ≠ {d}", "→ {a}   ← {b}   = {c}   ≠ {d}"),
    "Направление": ("Direction", "方向"),
    "Добавить текущую": ("Add current", "添加当前"),
    "Добавить текущую папку": ("Add current folder", "添加当前文件夹"),
    "Избранные папки": ("Favorites", "收藏夹"),
    "Избранные папки (hotlist)": ("Favorites (hotlist)", "收藏夹 (hotlist)"),
    "Изменить список…": ("Edit list…", "更改列表…"),
    "Выше": ("Up", "上移"),
    "Ниже": ("Down", "下移"),
    "Сохранить": ("Save", "保存"),
    "Добавить…": ("Add…", "添加…"),
    "Изменить…": ("Edit…", "更改…"),
    "Кнопки панели": ("Panel buttons", "面板按钮"),
    "Команда (%f — файл, %d — каталог):":
        ("Command (%f — file, %d — directory):", "命令（%f — 文件，%d — 目录）："),
    "Настроить кнопки панели": ("Configure panel buttons", "配置面板按钮"),
    "Название кнопки:": ("Button title:", "按钮名称："),
    "Плейсхолдеры: %f — файл под курсором, %d — каталог панели":
        ("Placeholders: %f — file under cursor, %d — panel directory",
         "占位符：%f — 光标处文件，%d — 面板目录"),
    "Панели указывают на один и тот же каталог":
        ("Panels point to the same directory", "两个面板指向同一目录"),
    "Поставлено в очередь: {n}": ("Queued: {n}", "已加入队列：{n}"),
    "Раскраска по типу": ("Colorize by type", "按类型着色"),
    "Размер (L)": ("Size (L)", "大小（左）"),
    "Размер (R)": ("Size (R)", "大小（右）"),
    "Изменён (L)": ("Modified (L)", "修改时间（左）"),
    "Изменён (R)": ("Modified (R)", "修改时间（右）"),
    "Нет отмеченных строк для синхронизации":
        ("No rows marked for sync", "未标记任何同步行"),
    "Сканирование…": ("Scanning…", "扫描中…"),
    "Синхронизация {n} объект(ов) {arrow} {dir}":
        ("Sync {n} item(s) {arrow} {dir}", "同步 {n} 个对象 {arrow} {dir}"),
    "Синхронизация работает для обычных каталогов":
        ("Sync works for regular directories only", "同步仅适用于普通目录"),
    "Синхронизация каталогов…": ("Sync directories…", "同步目录…"),
    "Синхронизация каталогов: {a} ⇄ {b}":
        ("Sync directories: {a} ⇄ {b}", "同步目录：{a} ⇄ {b}"),
    "Синхронизировать отмеченные": ("Sync marked", "同步所选"),
    "(без вывода)": ("(no output)", "（无输出）"),
    "-страницы.pdf": ("-страницы.pdf", "-страницы.pdf"),
    "CSV: кодировка {enc}, разделитель {delim!r}{note}":
        ("CSV: encoding {enc}, delimiter {delim!r}{note}",
         "CSV：编码 {enc}，分隔符 {delim!r}{note}"),
    "DOC • правка: Ctrl+S предложит сохранить как RTF/DOCX":
        ("DOC • edit: Ctrl+S will offer to save as RTF/DOCX",
         "DOC • 编辑：Ctrl+S 将提示另存为 RTF/DOCX"),
    "DOC • просмотр (antiword/catdoc) • F4 — правка":
        ("DOC • view (antiword/catdoc) • F4 — edit", "DOC • 查看（antiword/catdoc）• F4 — 编辑"),
    "DOC • текст": ("DOC • text", "DOC • 文本"),
    "HTML • кодировка: {enc}": ("HTML • encoding: {enc}", "HTML • 编码：{enc}"),
    "JSON некорректен": ("Invalid JSON", "JSON 无效"),
    "PDF: {total} стр. • масштаб {scale:.2f}x":
        ("PDF: {total} p. • zoom {scale:.2f}x", "PDF：共 {total} 页 • 缩放 {scale:.2f}x"),
    "PDF: страниц не осталось": ("PDF: no pages left", "PDF：没有剩余页面"),
    "RTF • правка: абзацы (форматирование упрощается)":
        ("RTF • edit: paragraphs (formatting is simplified)", "RTF • 编辑：段落（格式会被简化）"),
    "RTF • просмотр • F4 — правка": ("RTF • view • F4 — edit", "RTF • 查看 • F4 — 编辑"),
    "Sphaera Commander — двухпанельный файловый менеджер":
        ("Sphaera Commander — dual-panel file manager", "Sphaera Commander — 双栏文件管理器"),
    "XML • кодировка: {enc}": ("XML • encoding: {enc}", "XML • 编码：{enc}"),
    "cd: каталог не найден: {path}": ("cd: directory not found: {path}", "cd：未找到目录：{path}"),
    "docx: правка по абзацам (стиль абзаца сохраняется, встроенное форматирование меняемых абзацев теряется)":
        ("docx: edit by paragraphs (paragraph style is preserved, inline formatting of edited paragraphs is lost)",
         "docx：按段落编辑（保留段落样式，被编辑段落的内联格式会丢失）"),
    "docx: просмотр • F4 — правка по абзацам":
        ("docx: view • F4 — edit by paragraphs", "docx：查看 • F4 — 按段落编辑"),
    "epub: глав {n} • изображения показываются, CSS упрощён":
        ("epub: {n} chapters • images shown, CSS simplified", "epub：共 {n} 章 • 显示图片，CSS 已简化"),
    "pptx: {total} слайд(ов) • масштаб {scale:.2f}x • свой рендер":
        ("pptx: {total} slide(s) • zoom {scale:.2f}x • built-in renderer",
         "pptx：共 {total} 张幻灯片 • 缩放 {scale:.2f}x • 内置渲染"),
    "udisksctl не найден": ("udisksctl not found", "未找到 udisksctl"),
    "{action} {count} объект(ов) из\n{dir}?\n\n{names}":
        ("{action} {count} item(s) from\n{dir}?\n\n{names}",
         "将{action} {dir} 中的 {count} 个项目？\n\n{names}"),
    "{mode}: {path}": ("{mode}: {path}", "{mode}：{path}"),
    "{name} • кодировка: {enc}": ("{name} • encoding: {enc}", "{name} • 编码：{enc}"),
    "{old} — {err}": ("{old} — {err}", "{old} — {err}"),
    "{old} — назначение уже существует: {new}":
        ("{old} — destination already exists: {new}", "{old} — 目标已存在：{new}"),
    "{out}\nПерезаписать?": ("{out}\nOverwrite?", "{out}\n是否覆盖？"),
    "{path}:\n{message}": ("{path}:\n{message}", "{path}：\n{message}"),
    "Архив": ("Archive", "压缩包"),
    "Архив в панели назначения открыт только для чтения":
        ("The archive in the destination panel is read-only", "目标面板中的压缩包为只读"),
    "Было": ("Old name", "原名"),
    "Быстрый просмотр (Ctrl+Q)": ("Quick view (Ctrl+Q)", "快速查看（Ctrl+Q）"),
    "Быстрый просмотр (вторая панель)": ("Quick view (second panel)", "快速查看（第二面板）"),
    "В архив перетащить нельзя": ("Cannot drop into an archive", "无法拖放到压缩包中"),
    "В корзину": ("To trash", "移到回收站"),
    "В папке назначения уже есть {n} файл(ов) с такими именами:\n{names}\n\nПерезаписать?":
        ("The destination folder already contains {n} file(s) with these names:\n{names}\n\nOverwrite?",
         "目标文件夹中已有 {n} 个同名文件：\n{names}\n\n是否覆盖？"),
    "В файле ошибка JSON ({err}).\nСохранить как есть?":
        ("JSON error in the file ({err}).\nSave as is?", "文件中存在 JSON 错误（{err}）。\n仍要保存？"),
    "Все": ("All", "全部"),
    "Выберите архив (.zip, .tar, .tgz, .tar.bz2, .tar.xz)":
        ("Select an archive (.zip, .tar, .tgz, .tar.bz2, .tar.xz)",
         "请选择压缩包（.zip、.tar、.tgz、.tar.bz2、.tar.xz）"),
    "Выйти из полноэкранного режима": ("Exit full screen", "退出全屏"),
    "Выход": ("Exit", "退出"),
    "Где:": ("Where:", "位置："),
    "Групповое переименование": ("Batch rename", "批量重命名"),
    "Групповое переименование в архиве не поддерживается":
        ("Batch rename is not supported in archives", "压缩包内不支持批量重命名"),
    "Групповое переименование…": ("Batch rename…", "批量重命名…"),
    "Двоичный DOC перезаписать нельзя — сохранить как":
        ("Binary DOC cannot be overwritten — save as", "二进制 DOC 无法覆写 — 另存为"),
    "Двухпанельный файловый менеджер для Linux в духе Total Commander.<br><br>Tab — панели, F5/F6 — копирование/перенос, F3/F4 — просмотр/правка,<br>Alt+F7 — поиск, Ctrl+Q — быстрый просмотр, F8 — корзина.<br><br><span style='color:#5fbab4;'>Айдентика — Iustitia:</span> графит · пергамент · бирюза · бронза.":
        ("A dual-panel file manager for Linux in the spirit of Total Commander.<br><br>"
         "Tab — panels, F5/F6 — copy/move, F3/F4 — view/edit,<br>"
         "Alt+F7 — search, Ctrl+Q — quick view, F8 — trash.<br><br>"
         "<span style='color:#5fbab4;'>Identity — Iustitia:</span> graphite · parchment · teal · bronze.",
         "一款致敬 Total Commander 的 Linux 双栏文件管理器。<br><br>"
         "Tab — 切换面板，F5/F6 — 复制/移动，F3/F4 — 查看/编辑，<br>"
         "Alt+F7 — 搜索，Ctrl+Q — 快速查看，F8 — 回收站。<br><br>"
         "<span style='color:#5fbab4;'>视觉识别 — Iustitia：</span>石墨 · 羊皮纸 · 青碧 · 青铜。"),
    "Дерево": ("Tree", "树"),
    "Дерево JSON (Ctrl+T)": ("JSON tree (Ctrl+T)", "JSON 树（Ctrl+T）"),
    "Домой": ("Home", "主目录"),
    "Есть изменения": ("There are changes", "有更改"),
    "Закрыть приложение": ("Close application", "关闭应用"),
    "Заменить": ("Replace", "替换"),
    "Заменить на": ("Replace with", "替换为"),
    "Заменить…": ("Replace…", "替换…"),
    "Заменяемый (назначение)": ("Target (destination)", "被覆盖（目标）"),
    "Запаковать": ("Pack", "压缩"),
    "Запаковать…": ("Pack…", "压缩…"),
    "Запаковка": ("Packing", "正在压缩"),
    "Запись в архив {name}": ("Writing to archive {name}", "写入压缩包 {name}"),
    "Значение": ("Value", "值"),
    "Идёт файловая операция. Прервать её и выйти?":
        ("A file operation is running. Interrupt it and exit?", "文件操作正在进行。中断并退出？"),
    "Извлечение в другой архив не поддерживается":
        ("Extraction into another archive is not supported", "不支持解压到另一个压缩包"),
    "Извлечение из архива": ("Extract from archive", "从压缩包解压"),
    "Изменён": ("Modified", "修改时间"),
    "Имя архива (в {dir}):": ("Archive name (in {dir}):", "压缩包名称（在 {dir} 中）："),
    "Имя папки:": ("Folder name:", "文件夹名称："),
    "История — стрелки ↑/↓": ("History — ↑/↓ arrows", "历史记录 — ↑/↓ 方向键"),
    "Источник": ("Source", "源"),
    "Исходник": ("Source", "源码"),
    "Исходник/предпросмотр (Ctrl+Shift+P)":
        ("Source/preview (Ctrl+Shift+P)", "源码/预览（Ctrl+Shift+P）"),
    "КАТАЛОГ": ("DIRECTORY", "目录"),
    "Каталог": ("Directory", "目录"),
    "Каталог не найден:\n{target}": ("Directory not found:\n{target}", "未找到目录：\n{target}"),
    "Каталог.\n(Enter — перейти, F3 — открыть файл)":
        ("Directory.\n(Enter — open, F3 — view file)", "目录。\n（Enter — 进入，F3 — 查看文件）"),
    "Кодировка": ("Encoding", "编码"),
    "Командная строка": ("Command line", "命令行"),
    "Командная строка: Enter — выполнить (cd — сменить каталог панели)":
        ("Command line: Enter — run (cd — change panel directory)",
         "命令行：Enter — 执行（cd — 切换面板目录）"),
    "Копирование": ("Copy", "复制"),
    "Копирование (перетащено): {n}": ("Copy (dropped): {n}", "复制（拖放）：{n}"),
    "Копировать полный путь": ("Copy full path", "复制完整路径"),
    "Корень (/)": ("Root (/)", "根目录 (/)"),
    "Левая панель: смена диска": ("Left panel: change drive", "左面板：切换驱动器"),
    "Маска файлов:": ("File mask:", "文件掩码："),
    "Маски через пробел или ;:  *.py  *.txt;README*":
        ("Patterns separated by spaces or ;:  *.py  *.txt;README*",
         "掩码用空格或 ; 分隔：*.py  *.txt;README*"),
    "Монтирование": ("Mounting", "挂载"),
    "Найти": ("Find", "查找"),
    "Начало:": ("Start:", "起始："),
    "Не удалось извлечь:\n{exc}": ("Failed to extract:\n{exc}", "解压失败：\n{exc}"),
    "Не удалось открыть файл:\n{exc}": ("Failed to open file:\n{exc}", "无法打开文件：\n{exc}"),
    "Не удалось открыть:\n{exc}": ("Failed to open:\n{exc}", "无法打开：\n{exc}"),
    "Не удалось показать:\n{exc}": ("Cannot display:\n{exc}", "无法显示：\n{exc}"),
    "Не удалось построить план:\n{exc}": ("Failed to build plan:\n{exc}", "无法生成计划：\n{exc}"),
    "Не удалось сохранить RTF:\n{exc}": ("Failed to save RTF:\n{exc}", "保存 RTF 失败：\n{exc}"),
    "Не удалось сохранить XLS:\n{exc}": ("Failed to save XLS:\n{exc}", "保存 XLS 失败：\n{exc}"),
    "Не удалось сохранить docx:\n{exc}": ("Failed to save docx:\n{exc}", "保存 docx 失败：\n{exc}"),
    "Не удалось сохранить:\n{exc}": ("Failed to save:\n{exc}", "保存失败：\n{exc}"),
    "Неверный диапазон страниц": ("Invalid page range", "页面范围无效"),
    "Нет объекта под курсором": ("No item under cursor", "光标处没有项目"),
    "Нет объектов для обработки": ("Nothing to process", "没有可处理的项目"),
    "Нет объектов для переименования": ("Nothing to rename", "没有可重命名的项目"),
    "Нет отмеченных объектов (Insert — отметить)":
        ("Nothing marked (Insert to mark)", "没有标记的项目（Insert 标记）"),
    "Нет файла под курсором": ("No file under cursor", "光标处没有文件"),
    "Новая папка": ("New folder", "新建文件夹"),
    "Новое имя:": ("New name:", "新名称："),
    "Номер строки:": ("Line number:", "行号："),
    "О программе": ("About", "关于"),
    "Обновить": ("Refresh", "刷新"),
    "Обработано {done} из {total} объект(ов)":
        ("Processed {done} of {total} item(s)", "已处理 {total} 项中的 {done} 项"),
    "Обработано {done} из {total} объект(ов) — {done_size} из {total_size} ({pct}%)":
        ("Processed {done} of {total} item(s) — {done_size} of {total_size} ({pct}%)",
         "已处理 {total} 项中的 {done} 项 — {total_size} 中的 {done_size}（{pct}%）"),
    "Операция": ("Operation", "操作"),
    "Операция выполняется": ("Operation in progress", "操作正在执行"),
    "Операция завершена с ошибками: {n}.\nУспешно: {ok}, пропущено: {skipped}.":
        ("Operation finished with {n} error(s).\nSucceeded: {ok}, skipped: {skipped}.",
         "操作完成，出现 {n} 个错误。\n成功：{ok}，跳过：{skipped}。"),
    "Операция прервана.\nУспешно: {ok}, пропущено: {skipped}.":
        ("Operation cancelled.\nSucceeded: {ok}, skipped: {skipped}.",
         "操作已中断。\n成功：{ok}，跳过：{skipped}。"),
    "Открыть системным приложением": ("Open with system application", "用系统应用打开"),
    "Отмена": ("Cancel", "取消"),
    "Ошибка": ("Error", "错误"),
    "Ошибки операции": ("Operation errors", "操作错误"),
    "Папка": ("Folder", "文件夹"),
    "Перезаписать все": ("Overwrite all", "全部覆盖"),
    "Перезапись": ("Overwrite", "覆盖"),
    "Перезапись файлов": ("Overwrite files", "文件覆盖"),
    "Переименование": ("Renaming", "重命名"),
    "Переименование в архиве не поддерживается":
        ("Rename is not supported in archives", "压缩包内不支持重命名"),
    "Переименовано: {done}, ошибок: {n}.": ("Renamed: {done}, errors: {n}.", "已重命名：{done}，错误：{n}。"),
    "Переименовано: {n}": ("Renamed: {n}", "已重命名：{n}"),
    "Переименовать": ("Rename", "重命名"),
    "Перейти на строку": ("Go to line", "转到行"),
    "Переместить в корзину": ("Move to trash", "移到回收站"),
    "Перенос": ("Move", "移动"),
    "Перенос из архива": ("Move from archive", "从压缩包移动"),
    "Перенос строк": ("Word wrap", "自动换行"),
    "Переносить длинные строки": ("Wrap long lines", "换行显示长行"),
    "Перетаскивать нечего: объекты уже в этой папке":
        ("Nothing to move: items are already in this folder", "无需移动：项目已在此文件夹中"),
    "Переход": ("Go", "转到"),
    "Подключить {label} [{size}]": ("Mount {label} [{size}]", "挂载 {label} [{size}]"),
    "Подстановки:  * — имя без расширения,  [E] — расширение,  [N]/[N03] — счётчик (ширина)":
        ("Placeholders:  * — name without extension,  [E] — extension,  [N]/[N03] — counter (width)",
         "占位符：* — 不含扩展名的名称，[E] — 扩展名，[N]/[N03] — 计数器（宽度）"),
    "Поиск (Enter — далее)": ("Search (Enter — next)", "搜索（Enter — 下一个）"),
    "Поиск в: {dir}": ("Searching in: {dir}", "搜索位置：{dir}"),
    "Поиск файлов": ("Find files", "查找文件"),
    "Поиск файлов…": ("Find files…", "查找文件…"),
    "Полноэкранный режим": ("Full screen", "全屏"),
    "Поменять панели местами": ("Swap panels", "交换面板"),
    "Похоже, файл бинарный — встроенный редактор его не открывает.":
        ("The file looks binary — the built-in editor cannot open it.",
         "文件似乎是二进制文件 — 内置编辑器无法打开。"),
    "Правая панель: смена диска": ("Right panel: change drive", "右面板：切换驱动器"),
    "Правка": ("Edit", "编辑"),
    "Предпросмотр": ("Preview", "预览"),
    "Предыдущая команда ещё выполняется": ("Previous command is still running", "上一条命令仍在执行"),
    "Прервать все": ("Abort all", "全部中止"),
    "Прервать текущую": ("Cancel current", "取消当前任务"),
    "Примонтировано: {message}": ("Mounted: {message}", "已挂载：{message}"),
    "Пропустить все": ("Skip all", "全部跳过"),
    "Просмотр": ("View", "查看"),
    "Путь панели скопирован в буфер обмена": ("Panel path copied to clipboard", "面板路径已复制到剪贴板"),
    "Размер": ("Size", "大小"),
    "Размонтирование": ("Unmounting", "卸载"),
    "Размонтировано: {name}": ("Unmounted: {name}", "已卸载：{name}"),
    "Распаковать…": ("Unpack…", "解压…"),
    "Распаковка {name}": ("Unpacking {name}", "正在解压 {name}"),
    "Рег. выражение": ("Regex", "正则表达式"),
    "Рекурсивно": ("Recursive", "递归"),
    "Скопировано путей: {n}": ("Copied {n} path(s)", "已复制 {n} 个路径"),
    "Скрытые файлы": ("Hidden files", "隐藏文件"),
    "След. (Alt+↓) →": ("Next (Alt+↓) →", "下一个（Alt+↓）→"),
    "Смена диска": ("Change drive", "切换驱动器"),
    "Сначала извлеките объекты из архива":
        ("Extract the items from the archive first", "请先从压缩包中解压项目"),
    "Совпадение": ("Match", "匹配"),
    "Создание папок в архиве не поддерживается":
        ("Creating folders in archives is not supported", "压缩包内不支持创建文件夹"),
    "Сортировка": ("Sort", "排序"),
    "Сортировка: дата": ("Sort: date", "排序：日期"),
    "Сортировка: имя": ("Sort: name", "排序：名称"),
    "Сортировка: размер": ("Sort: size", "排序：大小"),
    "Сортировка: расширение": ("Sort: extension", "排序：扩展名"),
    "Сохранение XLS": ("Saving XLS", "保存 XLS"),
    "Сохранено страниц: {n}\n{out}": ("Saved {n} page(s)\n{out}", "已保存 {n} 页\n{out}"),
    "Сравнение каталогов работает для обычных каталогов":
        ("Directory comparison works for regular directories only", "目录比较仅适用于普通目录"),
    "Сравнение каталогов: {left} отличий слева, {right} справа (выделено синим)":
        ("Directory comparison: {left} differences on the left, {right} on the right (shown in blue)",
         "目录比较：左侧 {left} 处差异，右侧 {right} 处（以蓝色标出）"),
    "Сравнить каталоги": ("Compare directories", "比较目录"),
    "Станет": ("New name", "新名"),
    "Стоп": ("Stop", "停止"),
    "Стр.": ("Line", "行"),
    "Страницы (например 1-3,5; всего {total}):":
        ("Pages (e.g. 1-3,5; total {total}):", "页面（例如 1-3,5；共 {total} 页）："),
    "Таблица изменена. Сохранить XLS (формулы → значения)?":
        ("The sheet has been modified. Save XLS (formulas → values)?",
         "表格已修改。保存 XLS（公式 → 数值）？"),
    "Текст": ("Text", "文本"),
    "Текст не сохраняется в {enc} без потерь.\nСохранить как UTF-8?":
        ("Text cannot be saved in {enc} without loss.\nSave as UTF-8?",
         "文本无法无损保存为 {enc}。\n是否保存为 UTF-8？"),
    "Текст:": ("Text:", "文本："),
    "Удаление (в корзину)": ("Move to trash", "移到回收站"),
    "Удаление безвозвратно": ("Permanent deletion", "永久删除"),
    "Удаление из {name}": ("Delete from {name}", "从 {name} 删除"),
    "Удаление страницы": ("Delete page", "删除页面"),
    "Удалить": ("Delete", "删除"),
    "Удалить безвозвратно": ("Delete permanently", "永久删除"),
    "Удалить навсегда": ("Delete forever", "永久删除"),
    "Удалить страницу": ("Delete page", "删除页面"),
    "Удалить страницу {n} из {total}?":
        ("Delete page {n} of {total}?", "删除第 {n} 页（共 {total} 页）？"),
    "Узел": ("Node", "节点"),
    "Учитывать регистр": ("Case sensitive", "区分大小写"),
    "Файл": ("File", "文件"),
    "Файл изменён. Сохранить перед закрытием?":
        ("The file has been modified. Save before closing?", "文件已修改。关闭前保存？"),
    "Файл назначения уже существует. Заменить его?":
        ("The destination file already exists. Replace it?", "目标文件已存在。是否替换？"),
    "Файл существует": ("File exists", "文件已存在"),
    "Формат:": ("Format:", "格式："),
    "Форматировать": ("Format", "格式化"),
    "Форматировать JSON (Ctrl+Shift+F)": ("Format JSON (Ctrl+Shift+F)", "格式化 JSON（Ctrl+Shift+F）"),
    "Формулы и стили будут заменены значениями. Продолжить?":
        ("Formulas and styles will be replaced by values. Continue?",
         "公式和样式将替换为数值。是否继续？"),
    "Шаблон:": ("Pattern:", "模板："),
    "Шаг:": ("Step:", "步长："),
    "Экспорт": ("Export", "导出"),
    "Экспорт страниц": ("Export pages", "导出页面"),
    "Экспорт страниц PDF": ("Export PDF pages", "导出 PDF 页面"),
    "Экспорт страниц…": ("Export pages…", "导出页面…"),
    "Язык / Language / 语言": ("Язык / Language / 语言", "Язык / Language / 语言"),
    "Язык изменится после перезапуска приложения":
        ("The language will change after the application is restarted",
         "语言将在应用重启后生效"),
    "бинарный файл\n(F3 — hex-обзор)": ("binary file\n(F3 — hex view)", "二进制文件\n（F3 — 十六进制查看）"),
    "бинарный файл (hex-обзор)": ("binary file (hex view)", "二进制文件（十六进制查看）"),
    "бинарных пропущено: {n}": ("binary skipped: {n}", "跳过二进制文件：{n}"),
    "в книге нет листов": ("no sheets in the workbook", "工作簿中没有工作表"),
    "в книге нет текстовых глав": ("no text chapters in the book", "书中没有文本章节"),
    "в назначении каталог с тем же именем":
        ("a directory with the same name exists at destination", "目标处存在同名目录"),
    "в презентации нет слайдов": ("no slides in the presentation", "演示文稿中没有幻灯片"),
    "в файле нет страниц": ("the file has no pages", "文件没有页面"),
    "версия {v}": ("version {v}", "版本 {v}"),
    "внутренняя ошибка: {exc}": ("internal error: {exc}", "内部错误：{exc}"),
    "вхождений: {n}": ("hits: {n}", "匹配次数：{n}"),
    "дерева нет — ошибка: {exc}": ("no tree — error: {exc}", "无树视图 — 错误：{exc}"),
    "дерево недоступно: {exc}": ("tree unavailable: {exc}", "树视图不可用：{exc}"),
    "дубликат нового имени: {new!r} ({first!r} и {name!r})":
        ("duplicate new name: {new!r} ({first!r} and {name!r})",
         "新名称重复：{new!r}（{first!r} 和 {name!r}）"),
    "заменено: {n}": ("replaced: {n}", "已替换：{n}"),
    "изображение {w}×{h}": ("image {w}×{h}", "图像 {w}×{h}"),
    "каталог для левой (и правой) панели":
        ("directory for the left (and right) panel", "左（及右）面板的目录"),
    "кодировка: {enc}": ("encoding: {enc}", "编码：{enc}"),
    "крупных пропущено: {n}": ("large skipped: {n}", "跳过大文件：{n}"),
    "массив · {n}": ("array · {n}", "数组 · {n}"),
    "не найдено: {needle}": ("not found: {needle}", "未找到：{needle}"),
    "не отформатировано — ошибка (строка {line}, столбец {col}): {msg}":
        ("not formatted — error (line {line}, column {col}): {msg}",
         "未格式化 — 错误（第 {line} 行，第 {col} 列）：{msg}"),
    "не отформатировано: {exc}": ("not formatted: {exc}", "未格式化：{exc}"),
    "не удалось открыть ({kind}):\n{exc}": ("failed to open ({kind}):\n{exc}", "无法打开（{kind}）：\n{exc}"),
    "не удалось переместить в корзину": ("failed to move to trash", "无法移到回收站"),
    "не удалось прочитать каталог: {error}": ("failed to read directory: {error}", "读取目录失败：{error}"),
    "не удалось прочитать файл: {exc}": ("failed to read file: {exc}", "读取文件失败：{exc}"),
    "объект · {n}": ("object · {n}", "对象 · {n}"),
    "операций в очереди: {n}": ("operations queued: {n}", "队列中的任务：{n}"),
    "ошибка монтирования": ("mount error", "挂载错误"),
    "ошибка открытия": ("open error", "打开错误"),
    "ошибка размонтирования": ("unmount error", "卸载错误"),
    "ошибка чтения: {err}": ("read error: {err}", "读取错误：{err}"),
    "просмотрено файлов: {n}": ("files scanned: {n}", "已扫描文件：{n}"),
    "пусто — искать только по маске": ("empty — search by mask only", "留空 — 仅按掩码搜索"),
    "пустой шаблон": ("empty template", "模板为空"),
    "размонтировано": ("unmounted", "已卸载"),
    "слайд {n} из {total}": ("slide {n} of {total}", "第 {n} 张，共 {total} 张"),
    "совпало файлов: {n}": ("files matched: {n}", "匹配文件：{n}"),
    "сохранено (RTF)": ("saved (RTF)", "已保存（RTF）"),
    "сохранено (XLS, значения)": ("saved (XLS, values)", "已保存（XLS，数值）"),
    "сохранено (docx)": ("saved (docx)", "已保存（docx）"),
    "сохранено ({enc})": ("saved ({enc})", "已保存（{enc}）"),
    "сохранено: {name}": ("saved: {name}", "已保存：{name}"),
    "стр. {n} из {total}": ("p. {n} of {total}", "第 {n} 页，共 {total} 页"),
    "строк: {n}": ("rows: {n}", "行数：{n}"),
    "строка {line}, столбец {col}": ("line {line}, column {col}", "第 {line} 行，第 {col} 列"),
    "строка {line}, столбец {col}: {msg}":
        ("line {line}, column {col}: {msg}", "第 {line} 行，第 {col} 列：{msg}"),
    "утилита gio не найдена — корзина недоступна":
        ("gio utility not found — trash is unavailable", "未找到 gio 工具 — 回收站不可用"),
    "чтение {title}…": ("loading {title}…", "正在读取 {title}…"),
    "← Пред. (Alt+↑)": ("← Prev (Alt+↑)", "← 上一个（Alt+↑）"),
    "⏏ {label} ({mountpoint}) — размонтировать":
        ("⏏ {label} ({mountpoint}) — unmount", "⏏ {label}（{mountpoint}）— 卸载"),
    "⏳ чтение каталога…": ("⏳ reading directory…", "⏳ 正在读取目录…"),
    "Не удалось создать папку:\n{err}": ("Failed to create folder:\n{err}", "无法创建文件夹：\n{err}"),
    "Не удалось переименовать:\n{err}": ("Failed to rename:\n{err}", "重命名失败：\n{err}"),
    "Поиск файлов": ("Find files", "查找文件"),
    "файлов: {files}   папок: {dirs}   {size}":
        ("files: {files}   folders: {dirs}   {size}", "文件：{files}   文件夹：{dirs}   {size}"),
    "   •   {name}: {size} • {dirs} • {files}":
        ("   •   {name}: {size} • {dirs} • {files}", "   •   {name}：{size} • {dirs} • {files}"),
    "<КАТ>": ("<DIR>", "<目录>"),
    "Google Диск подключён (remote gdrive:)":
        ("Google Drive connected (gdrive:)", "Google 云端硬盘已连接（gdrive:）"),
    "rclone config create gdrive drive, затем вход в браузере":
        ("rclone config create gdrive drive, then sign in via browser",
         "rclone config create gdrive drive，然后在浏览器中登录"),
    "Выберите пару в таблице": ("Select a pair in the table", "请在表格中选择配对"),
    "Готово: {name}": ("Done: {name}", "完成：{name}"),
    "Добавить пару": ("Add pair", "添加配对"),
    "Дождитесь завершения синхронизации (или отмените)":
        ("Wait for the sync to finish (or cancel it)", "请等待同步完成（或取消）"),
    "Завершено с ошибкой (код {rc}): {name}":
        ("Finished with error (code {rc}): {name}", "完成但出错（代码 {rc}）：{name}"),
    "Закрыть": ("Close", "关闭"),
    "Каталоги {remote}:": ("Directories of {remote}:", "{remote} 的目录："),
    "Локальная папка": ("Local folder", "本地文件夹"),
    "Локальная папка пары должна быть каталогом":
        ("The local path of a pair must be a directory", "配对的本地路径必须是目录"),
    "На облаке нет подкаталогов (или не удалось прочитать)":
        ("No subdirectories on the cloud (or failed to list)", "云端没有子目录（或无法读取）"),
    "Не удалось подключить Google Диск (см. rclone config)":
        ("Failed to connect Google Drive (see rclone config)",
         "无法连接 Google 云端硬盘（见 rclone config）"),
    "Нет ни одного remote в rclone (rclone config)":
        ("No rclone remotes configured (rclone config)", "rclone 中没有任何 remote（rclone config）"),
    "Новая пара: текущая папка →": ("New pair: current folder →", "新配对：当前文件夹 →"),
    "Обзор на облаке": ("Browse on cloud", "在云端浏览"),
    "Обзор на облаке…": ("Browse on cloud…", "在云端浏览…"),
    "Облако": ("Cloud", "云端"),
    "Пара добавлена: {local} ↔ {remote}":
        ("Pair added: {local} ↔ {remote}", "已添加配对：{local} ↔ {remote}"),
    "Пары Ya.D убираются в самой ya.d (yad-sync remove)":
        ("Ya.D pairs are removed in ya.d itself (yad-sync remove)",
         "Ya.D 配对请在 ya.d 中移除（yad-sync remove）"),
    "Подключение Google Диска: откройте браузер и разрешите доступ…":
        ("Connecting Google Drive: open the browser and grant access…",
         "正在连接 Google 云端硬盘：请在浏览器中授权…"),
    "Подключить Google Диск": ("Connect Google Drive", "连接 Google 云端硬盘"),
    "Синхронизация с облаком": ("Cloud sync", "云同步"),
    "Синхронизация с облаком…": ("Cloud sync…", "云同步…"),
    "Синхронизация уже идёт — дождитесь завершения":
        ("A sync is already running — wait for it to finish",
         "同步已在进行 — 请等待完成"),
    "Синхронизация: {name} …": ("Syncing: {name} …", "正在同步：{name} …"),
    "Синхронизировать с облаком": ("Sync with cloud", "与云端同步"),
    "Синхронизировать сейчас": ("Sync now", "立即同步"),
    "Убрать пару": ("Remove pair", "移除配对"),
    "{title} — открыть": ("{title} — open", "{title} — 打开"),
    "⏏ {title} — отключить облачный диск":
        ("⏏ {title} — unmount cloud drive", "⏏ {title} — 卸载云端磁盘"),
    "{title} — подключить как диск":
        ("{title} — mount as drive", "{title} — 挂载为磁盘"),
    "⏳ подключение облачного диска {title}…":
        ("⏳ mounting cloud drive {title}…", "⏳ 正在挂载云端磁盘 {title}…"),
    "Не удалось подключить {title}: {err}":
        ("Failed to mount {title}: {err}", "无法挂载 {title}：{err}"),
    "{title} подключён: {path}": ("{title} mounted: {path}", "{title} 已挂载：{path}"),
    "Не удалось отключить: {err}": ("Failed to unmount: {err}", "无法卸载：{err}"),
    "{title} отключён": ("{title} unmounted", "{title} 已卸载"),
    "Облачный диск": ("Cloud drive", "云端磁盘"),
    "rclone не найден": ("rclone not found", "未找到 rclone"),
    "монтирование не поднялось": ("the mount did not come up", "挂载未能建立"),
    "fusermount3 не найден": ("fusermount3 not found", "未找到 fusermount3"),
    "Быстрый поиск: {prefix}*": ("Quick search: {prefix}*", "快速搜索：{prefix}*"),
    "Быстрый поиск: {prefix}* — нет совпадений": ("Quick search: {prefix}* — no match", "快速搜索：{prefix}* — 无匹配"),
    "Веточный просмотр: {name}": ("Branch view: {name}", "分支视图：{name}"),
    "Веточный просмотр…": ("Branch view…", "分支视图…"),
    "Во временной панели: {n} объект(ов)": ("Temp panel: {n} item(s)", "临时面板中：{n} 个项目"),
    "Временная панель": ("Temp panel", "临时面板"),
    "Временная панель…": ("Temp panel…", "临时面板…"),
    "Диски и облака (меню подключения)": ("Drives and clouds (connect menu)", "磁盘和云（连接菜单）"),
    "Добавить во временную панель": ("Add to temp panel", "添加到临时面板"),
    "Закрыть вкладку": ("Close tab", "关闭标签页"),
    "Коллекция пуста": ("The collection is empty", "集合为空"),
    "Копировать в другую панель": ("Copy to the other panel", "复制到另一面板"),
    "Левая панель: диски (NC)": ("Left panel: drives (NC)", "左面板：磁盘（NC）"),
    "Модуль {name}: {err}": ("Module {name}: {err}", "模块 {name}：{err}"),
    "Назначение — архив (только чтение)": ("Destination is an archive (read-only)", "目标为压缩包（只读）"),
    "Новая вкладка": ("New tab", "新建标签页"),
    "Объектов: {n} ({size})": ("Items: {n} ({size})", "项目：{n}（{size}）"),
    "Очистить": ("Clear", "清空"),
    "Перенести в другую панель": ("Move to the other panel", "移动到另一面板"),
    "Последнюю вкладку закрыть нельзя": ("The last tab cannot be closed", "不能关闭最后一个标签页"),
    "Правая панель: диски (NC)": ("Right panel: drives (NC)", "右面板：磁盘（NC）"),
    "Следующая вкладка": ("Next tab", "下一个标签页"),
    "Сохранить список…": ("Save list…", "保存列表…"),
    "Список сохранён: {path}": ("List saved: {path}", "列表已保存：{path}"),
    "Убрать из списка": ("Remove from list", "从列表移除"),
    "Файлов: {n}": ("Files: {n}", "文件数：{n}"),
    "Чтение…": ("Reading…", "正在读取…"),
    "SFTP: не проверять ключ хоста (домашняя сеть)": ("SFTP: skip host key check (home network)", "SFTP：不校验主机密钥（家庭网络）"),
    "rclone не смог скрыть пароль": ("rclone failed to obscure the password", "rclone 无法加密密码"),
    "Адрес (хост):": ("Address (host):", "地址（主机）："),
    "Архивные профили": ("Archive profiles", "存档配置"),
    "Архивные профили…": ("Archive profiles…", "存档配置…"),
    "В папке нет файлов сумм (.md5/.sha256)": ("No checksum files (.md5/.sha256) in the folder", "文件夹中没有校验和文件（.md5/.sha256）"),
    "Введите адрес сервера": ("Enter the server address", "请输入服务器地址"),
    "Всего: {files} • {dirs} • {size}": ("Total: {files} • {dirs} • {size}", "合计：{files} • {dirs} • {size}"),
    "Выберите профиль": ("Select a profile", "请选择配置"),
    "Готово: {path}": ("Done: {path}", "完成：{path}"),
    "Имя архива:": ("Archive name:", "存档名称："),
    "Имя профиля:": ("Profile name:", "配置名称："),
    "Исключения (через запятую):": ("Excludes (comma-separated):", "排除（逗号分隔）："),
    "Не совпало: {n} — {details}": ("Mismatched: {n} — {details}", "不匹配：{n} — {details}"),
    "Не удалось создать remote {name}": ("Failed to create remote {name}", "无法创建 remote {name}"),
    "Нет записи в файлах сумм: {n}": ("No checksum-file entry for: {n}", "校验和文件中没有记录：{n}"),
    "Объект": ("Item", "项目"),
    "Отмеченные объекты активной панели будут упакованы": ("Marked items of the active panel will be packed", "将打包活动面板中标记的项目"),
    "Ошибка упаковки: {err}": ("Packing error: {err}", "打包错误：{err}"),
    "Пароль:": ("Password:", "密码："),
    "Подключить сервер": ("Connect server", "连接服务器"),
    "Подключить сервер (FTP/SFTP/WebDAV)…": ("Connect server (FTP/SFTP/WebDAV)…", "连接服务器（FTP/SFTP/WebDAV）…"),
    "Подпапки": ("Subfolders", "子文件夹"),
    "Пользователь:": ("User:", "用户："),
    "Порт (пусто — по умолчанию):": ("Port (empty — default):", "端口（留空为默认）："),
    "Проверено: {n} — все суммы совпали": ("Verified: {n} — all checksums match", "已校验：{n} — 全部一致"),
    "Проверить по файлу сумм": ("Verify against checksum file", "按校验和文件校验"),
    "Сервер {name} добавлен — монтирую как диск…": ("Server {name} added — mounting as a drive…", "服务器 {name} 已添加 — 正在挂载为磁盘…"),
    "Создать и подключить как диск": ("Create and mount as drive", "创建并挂载为磁盘"),
    "Создаю подключение…": ("Creating the connection…", "正在创建连接…"),
    "Сохранить профиль": ("Save profile", "保存配置"),
    "Статистика папки: {name}": ("Folder statistics: {name}", "文件夹统计：{name}"),
    "Статистика папки…": ("Folder statistics…", "文件夹统计…"),
    "Тип:": ("Type:", "类型："),
    "Удалить профиль": ("Delete profile", "删除配置"),
    "Упаковать отмеченные по профилю": ("Pack marked by profile", "按配置打包所标记"),
    "Упаковать по профилю {name}": ("Pack by profile {name}", "按配置 {name} 打包"),
    "Упаковка…": ("Packing…", "正在打包…"),
    "Файлы": ("Files", "文件"),
    "age не установлен (sudo dnf install age)": ("age is not installed (sudo dnf install age)", "未安装 age（sudo dnf install age）"),
    "Дубликаты между панелями": ("Cross-panel duplicates", "跨面板重复文件"),
    "Дубликаты между панелями…": ("Cross-panel duplicates…", "跨面板重复文件…"),
    "Ключ age не создан (age-keygen)": ("age key not created (age-keygen)", "未创建 age 密钥（age-keygen）"),
    "Ключ age создан: {key}": ("age key created: {key}", "age 密钥已创建：{key}"),
    "Ключ: {key}": ("Key: {key}", "密钥：{key}"),
    "Панель": ("Panel", "面板"),
    "Раздаётся с записью (WebDAV). Не закрывайте окно.": ("Sharing with write access (WebDAV). Do not close this window.", "共享中（可写 WebDAV）。请勿关闭此窗口。"),
    "Разрешить запись (WebDAV)": ("Allow writing (WebDAV)", "允许写入（WebDAV）"),
    "Создать ключ age": ("Create age key", "创建 age 密钥"),
    "не создан": ("not created", "未创建"),
    "gpg не найден (sudo dnf install gnupg2)": ("gpg not found (sudo dnf install gnupg2)", "未找到 gpg（sudo dnf install gnupg2）"),
    "Введите парольную фразу": ("Enter a passphrase", "输入密码短语"),
    "Выполнить": ("Run", "执行"),
    "Готово: {n}": ("Done: {n}", "完成：{n}"),
    "Готово: {ok}, с ошибками: {n}": ("Done: {ok}, with errors: {n}", "完成：{ok}，出错：{n}"),
    "Групп дубликатов: {g}, лишнего: {w}": ("Duplicate groups: {g}, wasted: {w}", "重复组：{g}，冗余：{w}"),
    "Группа": ("Group", "组"),
    "Зашифровать": ("Encrypt", "加密"),
    "Зашифровать/расшифровать…": ("Encrypt/decrypt…", "加密/解密…"),
    "Зашифровать…": ("Encrypt…", "加密…"),
    "Не запущено": ("Not running", "未运行"),
    "Не удалось занять порт: {err}": ("Could not bind the port: {err}", "无法占用端口：{err}"),
    "Не удалось удалить часть файлов": ("Failed to delete some files", "部分文件删除失败"),
    "Нет подходящих файлов": ("No suitable files", "没有合适的文件"),
    "Остановить": ("Stop", "停止"),
    "Отчёт сохранён: {path}": ("Report saved: {path}", "报告已保存：{path}"),
    "Папка: {dir}": ("Folder: {dir}", "文件夹：{dir}"),
    "Парольная фраза:": ("Passphrase:", "密码短语："),
    "Повторите:": ("Repeat:", "重复："),
    "Поиск дубликатов: {name}": ("Duplicate search: {name}", "重复文件搜索：{name}"),
    "Поиск дубликатов…": ("Find duplicates…", "查找重复文件…"),
    "Порт — число от 1 до 65535": ("Port must be a number from 1 to 65535", "端口必须是 1 到 65535 的数字"),
    "Порт:": ("Port:", "端口："),
    "Работаю…": ("Working…", "正在处理…"),
    "Раздавать": ("Share", "共享"),
    "Раздать папку по сети…": ("Share folder over network…", "通过网络共享文件夹…"),
    "Раздача папки по сети": ("Folder sharing over network", "通过网络共享文件夹"),
    "Раздаётся (только чтение). Не закрывайте окно.": ("Sharing (read-only). Do not close this window.", "共享中（只读）。请勿关闭此窗口。"),
    "Расшифровать": ("Decrypt", "解密"),
    "Расшифровать…": ("Decrypt…", "解密…"),
    "Сохранить отчёт…": ("Save report…", "保存报告…"),
    "Удалено в корзину": ("Moved to trash", "已移到回收站"),
    "Удалить выбранные (в корзину)": ("Delete selected (to trash)", "删除所选（到回收站）"),
    "Удалить исходный файл после операции": ("Delete the original file after the operation", "操作后删除原始文件"),
    "Фразы не совпадают": ("Passphrases do not match", "密码短语不一致"),
    "локально: http://localhost:{port}/": ("locally: http://localhost:{port}/", "本地：http://localhost:{port}/"),
    "… и ещё {n}": ("… and {n} more", "… 还有 {n} 个"),
    "&Инструменты": ("&Tools", "工具(&T)"),
    "CRC32 не совпал: файл собран, но повреждён": ("CRC32 mismatch: the file was assembled but is damaged", "CRC32 不匹配：文件已组装但已损坏"),
    "{a}  ↔  {b}": ("{a}  ↔  {b}", "{a}  ↔  {b}"),
    "Алгоритм:": ("Algorithm:", "算法："),
    "В папке нет файла сумм {ext}": ("No {ext} checksum file in the folder", "文件夹中没有校验和文件 {ext}"),
    "В папке нет частей или файла .crc": ("No parts or .crc file in the folder", "文件夹中没有分卷或 .crc 文件"),
    "Включён": ("Enabled", "已启用"),
    "Все суммы совпали ({total})": ("All checksums match ({total})", "全部校验和一致（{total}）"),
    "Вычисление…": ("Computing…", "正在计算…"),
    "Вычислить": ("Compute", "计算"),
    "Готово: {n} сумм": ("Done: {n} checksums", "完成：{n} 个校验和"),
    "Контрольные суммы": ("Checksums", "校验和"),
    "Контрольные суммы…": ("Checksums…", "校验和…"),
    "Модуль": ("Module", "模块"),
    "Модуль {name} не загрузился: {err}": ("Module {name} failed to load: {err}", "模块 {name} 加载失败：{err}"),
    "Открыть терминал здесь": ("Open terminal here", "在此处打开终端"),
    "Отметьте ровно два файла (Insert/Ctrl+клик)": ("Mark exactly two files (Insert/Ctrl+click)", "请恰好标记两个文件（Insert/Ctrl+点击）"),
    "Первое отличие по смещению {offset}\n\n": ("First difference at offset {offset}\n\n", "首个差异位于偏移 {offset}\n\n"),
    "Плагины": ("Plugins", "插件"),
    "Плагины…": ("Plugins…", "插件…"),
    "Пользовательские модули: {dir} — применится после перезапуска": ("User modules: {dir} — applied after restart", "用户模块：{dir} — 重启后生效"),
    "Проверить файл сумм…": ("Verify checksum file…", "校验校验和文件…"),
    "Проверка: {total}, не совпало: {bad}, нет файла: {miss}": ("Checked: {total}, mismatched: {bad}, missing: {miss}", "检查：{total}，不匹配：{bad}，缺少文件：{miss}"),
    "Разделено на {n} частей + .crc": ("Split into {n} parts + .crc", "已分割为 {n} 个分卷 + .crc"),
    "Разделить": ("Split", "分割"),
    "Разделить / собрать файл": ("Split / combine file", "分割 / 合并文件"),
    "Разделить / собрать файл…": ("Split / combine file…", "分割 / 合并文件…"),
    "Размеры различаются: {sa} и {sb} байт": ("Sizes differ: {sa} and {sb} bytes", "大小不同：{sa} 和 {sb} 字节"),
    "Собрано: {name} ({parts} ч.) — CRC32 OK": ("Combined: {name} ({parts} parts) — CRC32 OK", "已合并：{name}（{parts} 个分卷）— CRC32 正确"),
    "Собрать (по .crc или первой части)": ("Combine (by .crc or first part)", "合并（按 .crc 或第一个分卷）"),
    "Сохранено: {path}": ("Saved: {path}", "已保存：{path}"),
    "Сохранить в файл сумм": ("Save to checksum file", "保存到校验和文件"),
    "Сравнение по содержимому": ("Content comparison", "内容比较"),
    "Сравнить по содержимому": ("Compare by content", "按内容比较"),
    "Сравнить по содержимому…": ("Compare by content…", "按内容比较…"),
    "Сумма": ("Checksum", "校验和"),
    "Файлы идентичны": ("The files are identical", "文件完全相同"),
    "Файлы различаются с байта {offset}": ("The files differ starting at byte {offset}", "文件从字节 {offset} 起不同"),
    "Часть:": ("Part size:", "分卷大小："),
    "встроенный": ("built-in", "内置"),
    "пользовательский": ("user", "用户"),
    "прервано": ("cancelled", "已取消"),
    "размер не совпал: {have} из {want}": ("size mismatch: {have} of {want}", "大小不匹配：{want} 中为 {have}"),
    "своя, МиБ": ("custom, MiB", "自定义，MiB"),
    "терминал не найден (kgx/gnome-terminal/konsole/xterm)": ("no terminal found (kgx/gnome-terminal/konsole/xterm)", "未找到终端（kgx/gnome-terminal/konsole/xterm）"),
    "части не найдены рядом с {name}": ("no parts found next to {name}", "在 {name} 附近未找到分卷"),
    "Имя": ("Name", "名称"),
    "Расш.": ("Ext", "扩展名"),
    "Размер": ("Size", "大小"),
    "Изменён": ("Modified", "修改时间"),
    "Права": ("Perms", "权限"),
    " • без пересчёта: {n}":
        (" • not recalculated: {n}", " • 未能重算：{n}"),
    "Аннотации": ("Annotations", "批注"),
    "Аннотации PDF": ("PDF annotations", "PDF 批注"),
    "Аннотации:": ("Annotations:", "批注："),
    "Аннотации…": ("Annotations…", "批注…"),
    "Выделение": ("Highlight", "高亮"),
    "Выделить фрагмент: обведите его мышью":
        ("Highlight a fragment: drag a rectangle over it",
         "高亮片段：用鼠标框选"),
    "Добавить видимый текст: обведите область":
        ("Add visible text: drag a rectangle for it",
         "添加可见文字：框选区域"),
    "Заметка": ("Note", "便签"),
    "Миниатюры (картинки и PDF)":
        ("Thumbnails (images and PDF)", "缩略图（图片和 PDF）"),
    "Не удалось добавить: {exc}": ("Could not add: {exc}", "无法添加：{exc}"),
    "Не удалось сохранить xlsx:\n{exc}":
        ("Could not save the xlsx:\n{exc}", "无法保存 xlsx：\n{exc}"),
    "Не удалось удалить: {exc}": ("Could not delete: {exc}", "无法删除：{exc}"),
    "Перенос (перетащено): {n}": ("Move (dropped): {n}", "移动（拖放）：{n}"),
    "Показывать формулы вместо значений":
        ("Show formulas instead of values", "显示公式而非数值"),
    "Прикрепить заметку: клик по странице":
        ("Attach a note: click on the page", "添加便签：在页面上点击"),
    "Размеры каталогов": ("Directory sizes", "目录大小"),
    "Размеры каталогов: ": ("Directory sizes: ", "目录大小："),
    "Светлая тема (пергамент)": ("Light theme (parchment)", "浅色主题（羊皮纸）"),
    "Системная тема": ("System theme", "系统主题"),
    "Страница": ("Page", "页"),
    "Таблица изменена. Сохранить xlsx (формулы сохраняются)?":
        ("The sheet was modified. Save the xlsx (formulas are kept)?",
         "表格已修改。保存 xlsx（保留公式）？"),
    "Текст заметки:": ("Note text:", "便签内容："),
    "Текст на странице": ("Text on page", "页面文字"),
    "Тема": ("Theme", "主题"),
    "Тема: {name}": ("Theme: {name}", "主题：{name}"),
    "Тип": ("Type", "类型"),
    "Тёмная тема (Iustitia)": ("Dark theme (Iustitia)", "深色主题（Iustitia）"),
    "Удалить выбранные": ("Delete selected", "删除所选"),
    "включены": ("on", "开"),
    "выключены": ("off", "关"),
    "сохранено (xlsx{note})": ("saved (xlsx{note})", "已保存（xlsx{note}）"),
    "строк: {n} • формул: {f}":
        ("rows: {n} • formulas: {f}", "行：{n} • 公式：{f}"),
    "Предыдущая страница": ("Previous page", "上一页"),
    "Следующая страница": ("Next page", "下一页"),
    "стр. {n} из {total} • {scale:.0f}%":
        ("p. {n} of {total} • {scale:.0f}%", "第 {n} 页，共 {total} 页 • {scale:.0f}%"),
    "{name} • стр. {n} из {count} • F3 — все страницы":
        ("{name} • p. {n} of {count} • F3 for all pages",
         "{name} • 第 {n} 页，共 {count} 页 • F3 查看全部"),
    "{name} • слайд {n} из {count} • F3":
        ("{name} • slide {n} of {count} • F3", "{name} • 第 {n} 张，共 {count} 张 • F3"),
    "Листание: кнопки или клик по краю страницы. Масштаб: колесо с Ctrl или щипок на тачпаде.":
        ("Paging: buttons or click a page edge. Zoom: Ctrl+wheel or touchpad pinch.",
         "翻页：按钮或点击页面边缘。缩放：Ctrl+滚轮或触控板双指捏合。"),
    "{date} — ГГГГ-ММ-ДД, {time} — ЧЧММСС, {orig} — исходное имя, {n} — счётчик":
        ("{date} — YYYY-MM-DD, {time} — HHMMSS, {orig} — original name, {n} — counter",
         "{date} — YYYY-MM-DD，{time} — HHMMSS，{orig} — 原名，{n} — 序号"),
    "{files} файл(ов), {dirs} подпапок":
        ("{files} file(s), {dirs} subfolders", "{files} 个文件，{dirs} 个子文件夹"),
    "{out}\nуже существует": ("{out}\nalready exists", "{out}\n已存在"),
    "Анализ места в архиве не считается":
        ("Disk usage analysis is not available inside archives",
         "归档内无法进行空间分析"),
    "Анализ места: {name}": ("Disk usage: {name}", "空间分析：{name}"),
    "Анализ места…": ("Disk usage…", "空间分析…"),
    "Владелец": ("Owner", "所有者"),
    "Вперёд (история панели)": ("Forward (panel history)", "前进（面板历史）"),
    "Выберите картинки (png/jpg/webp/bmp/tiff)":
        ("Select images (png/jpg/webp/bmp/tiff)", "选择图片（png/jpg/webp/bmp/tiff）"),
    "Доля": ("Share", "占比"),
    "Доступ": ("Accessed", "访问时间"),
    "Жёсткая ссылка": ("Hard link", "硬链接"),
    "Жёсткая ссылка создана: {path}": ("Hard link created: {path}", "硬链接已创建：{path}"),
    "Жёсткая ссылка — только для файла под курсором":
        ("A hard link applies to the file under the cursor only",
         "硬链接仅适用于光标所在文件"),
    "Жёстких ссылок": ("Hard links", "硬链接数"),
    "Запись": ("Write", "写入"),
    "Имя ссылки (цель: {target}):":
        ("Link name (target: {target}):", "链接名称（目标：{target}）："),
    "Имя файла:": ("File name:", "文件名："),
    "Исполнение": ("Execute", "执行"),
    "История панели": ("Panel history", "面板历史"),
    "Каталог назначения не найден: {path}":
        ("Destination folder not found: {path}", "未找到目标文件夹：{path}"),
    "Качество:": ("Quality:", "质量："),
    "Конвертация в архиве не поддерживается":
        ("Conversion inside archives is not supported", "归档内不支持转换"),
    "Конвертация картинок: {n}": ("Image conversion: {n}", "图片转换：{n}"),
    "Конвертировать": ("Convert", "转换"),
    "Конвертировать картинки": ("Convert images", "转换图片"),
    "Конвертировать картинки…": ("Convert images…", "转换图片…"),
    "Конвертировать нечего (цели совпадают с источниками)":
        ("Nothing to convert (targets match sources)",
         "无可转换（目标与源相同）"),
    "Куда:": ("Destination:", "目标位置："),
    "Место на диске": ("Size on disk", "占用空间"),
    "На уровень выше": ("One level up", "上一级"),
    "Назад (история панели)": ("Back (panel history)", "后退（面板历史）"),
    "Не удалось сменить права: {exc}":
        ("Could not change permissions: {exc}", "无法更改权限：{exc}"),
    "Не удалось сменить права: {name}: {exc}":
        ("Could not change permissions: {name}: {exc}",
         "无法更改权限：{name}：{exc}"),
    "Не удалось создать ссылку:\n{err}":
        ("Could not create the link:\n{err}", "无法创建链接：\n{err}"),
    "Не удалось создать файл:\n{err}":
        ("Could not create the file:\n{err}", "无法创建文件：\n{err}"),
    "Новый файл": ("New file", "新建文件"),
    "Обзор…": ("Browse…", "浏览…"),
    "Одноимённые файлы сверяются хэшами SHA-256, а не размером и датой":
        ("Same-name files are compared by SHA-256 hashes, not size and date",
         "同名文件按 SHA-256 哈希比较，而非大小和日期"),
    "Остальные": ("Others", "其他"),
    "Переименовать по EXIF-дате": ("Rename by EXIF date", "按 EXIF 日期重命名"),
    "Переименовать по EXIF-дате…": ("Rename by EXIF date…", "按 EXIF 日期重命名…"),
    "Права (буквы)": ("Permissions (letters)", "权限（字母）"),
    "Применить права": ("Apply permissions", "应用权限"),
    "Применить рекурсивно (вложенные)":
        ("Apply recursively (nested items)", "递归应用（含嵌套项）"),
    "Свойства": ("Properties", "属性"),
    "Свойства в архиве не показываются":
        ("Properties are not available inside archives", "归档内不显示属性"),
    "Свойства: {name}": ("Properties: {name}", "属性：{name}"),
    "Символьная ссылка": ("Symbolic link", "符号链接"),
    "Содержимое": ("Contents", "内容"),
    "Создан/метаданные": ("Created/metadata", "创建/元数据"),
    "Создание файлов в архиве не поддерживается":
        ("Creating files inside archives is not supported", "归档内不支持创建文件"),
    "Создать жёсткую ссылку…": ("Create hard link…", "创建硬链接…"),
    "Создать символьную ссылку…": ("Create symbolic link…", "创建符号链接…"),
    "Создать файл": ("Create file", "创建文件"),
    "Сравнивать по содержимому (медленнее)":
        ("Compare by content (slower)", "按内容比较（较慢）"),
    "Ссылка на {name}": ("Link to {name}", "链接到 {name}"),
    "Ссылки в архиве не создаются":
        ("Links cannot be created inside archives", "归档内无法创建链接"),
    "Суффикс имени:": ("Name suffix:", "名称后缀："),
    "Указывает на": ("Points to", "指向"),
    "Чтение": ("Read", "读取"),
    "не удалось записать {fmt}": ("could not write {fmt}", "无法写入 {fmt}"),
    "не удалось прочитать изображение":
        ("could not read the image", "无法读取图片"),
    "объектов: {n} • файл(ов): {f} • {size}":
        ("items: {n} • file(s): {f} • {size}", "条目：{n} • 文件：{f} • {size}"),
    "папка": ("folder", "文件夹"),
    "переименуется: {n} из {total}":
        ("to be renamed: {n} of {total}", "将重命名：{n}/{total}"),
    "символьная ссылка": ("symbolic link", "符号链接"),
    "файл": ("file", "文件"),
    "файлов к конвертации: {n}":
        ("files to convert: {n}", "待转换文件：{n}"),
    "файлов: {n}": ("files: {n}", "文件：{n}"),
    "цель уже существует": ("target already exists", "目标已存在"),
}


_STRINGS = {key: {"en": en, "zh": zh} for key, (en, zh) in _T.items()}

# ключи, передаваемые в tr() не константой (индексированные таблицы и т.п.);
# тест полноты не считает их «мёртвыми»
DYNAMIC_KEYS = frozenset({"Имя", "Расш.", "Размер", "Изменён", "Права", "<КАТ>"})


def tr(s: str) -> str:
    """Перевод строки-ключа; без перевода — ключ (по-русски)."""
    if LANG == "ru":
        return s
    return _STRINGS.get(s, {}).get(LANG, s)


def plural(n: int, one: str, few: str, many: str) -> str:
    """Русские формы: 1 файл / 2 файла / 5 файлов."""
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} {one}"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} {few}"
    return f"{n} {many}"


def unit(n: int, ru: tuple[str, str, str], en: tuple[str, str], zh: str) -> str:
    """Число + единица на текущем языке: «5 файлов» / «5 files» / «5 个文件»."""
    if LANG == "ru":
        return plural(n, *ru)
    if LANG == "en":
        return f"{n} {en[0] if n == 1 else en[1]}"
    return f"{n} {zh}"


def all_keys() -> list[str]:
    """Все ключи словаря (для теста полноты)."""
    return sorted(_STRINGS)
