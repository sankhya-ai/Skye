//! Finding an edit's `old` text when the model got the whitespace wrong.
//!
//! The commonest reason a weaker model's `StrReplaceFile` misses is not a
//! wrong idea but wrong whitespace: trailing spaces, tabs for spaces, or the
//! whole block indented one level off. When the exact text is absent, the
//! edit is matched line by line in two looser passes:
//!
//! 1. lines equal after trimming trailing whitespace;
//! 2. lines equal after trimming both ends — the replacement is then
//!    re-indented by the difference between the file's indentation and the
//!    model's, so the new code lands at the file's indentation.
//!
//! A looser pass is used only when it finds exactly one place. Two candidate
//! places mean the edit is ambiguous, and it fails as before.

/// Where a tolerant match landed, and the rewritten file.
#[derive(Debug, PartialEq, Eq)]
pub struct Tolerant {
    pub content: String,
    /// 1-based line numbers of the replaced block.
    pub first_line: usize,
    pub last_line: usize,
    pub reindented: bool,
}

/// Replace `old` with `new` in `content` by a unique whitespace-tolerant match.
pub fn replace(content: &str, old: &str, new: &str) -> Option<Tolerant> {
    let old_lines: Vec<&str> = old.trim_matches('\n').lines().collect();
    if old_lines.iter().all(|l| l.trim().is_empty()) {
        return None;
    }
    let file_lines: Vec<&str> = content.split_inclusive('\n').collect();
    let bare = |line: &str| line.trim_end_matches(['\n', '\r']).to_string();

    let trailing = find_unique(&file_lines, &old_lines, |a, b| bare(a).trim_end() == b.trim_end());
    let (start, reindent) = match trailing {
        Some(start) => (start, None),
        None => {
            let start = find_unique(&file_lines, &old_lines, |a, b| bare(a).trim() == b.trim())?;
            let file_indent = indentation(&bare(file_lines[start + first_code(&old_lines)]));
            let model_indent = indentation(old_lines[first_code(&old_lines)]);
            (start, Some((model_indent, file_indent)))
        }
    };
    let end = start + old_lines.len();
    let ending = if content.contains("\r\n") { "\r\n" } else { "\n" };
    let mut replacement = match &reindent {
        Some((from, to)) if from != to => new.lines().map(|line| reindent_line(line, from, to)).collect::<Vec<_>>().join(ending),
        _ => new.lines().collect::<Vec<_>>().join(ending),
    };
    // Keep the line break that ended the replaced block.
    if file_lines[end - 1].ends_with('\n') && !new.is_empty() {
        replacement.push_str(ending);
    }
    let mut out = String::with_capacity(content.len() + new.len());
    out.extend(file_lines[..start].iter().copied());
    out.push_str(&replacement);
    out.extend(file_lines[end..].iter().copied());
    Some(Tolerant {
        content: out,
        first_line: start + 1,
        last_line: end,
        reindented: matches!(reindent, Some((ref from, ref to)) if from != to),
    })
}

fn find_unique(file: &[&str], old: &[&str], eq: impl Fn(&str, &str) -> bool) -> Option<usize> {
    if old.len() > file.len() {
        return None;
    }
    let mut found = None;
    for start in 0..=file.len() - old.len() {
        if old.iter().enumerate().all(|(i, line)| eq(file[start + i], line)) {
            if found.is_some() {
                return None;
            }
            found = Some(start);
        }
    }
    found
}

fn first_code(lines: &[&str]) -> usize {
    lines.iter().position(|l| !l.trim().is_empty()).unwrap_or(0)
}

fn indentation(line: &str) -> String {
    line.chars().take_while(|c| *c == ' ' || *c == '\t').collect()
}

fn reindent_line(line: &str, from: &str, to: &str) -> String {
    if line.trim().is_empty() {
        return String::new();
    }
    match line.strip_prefix(from) {
        Some(rest) => format!("{to}{rest}"),
        // Less indented than the block's first line: keep its own indentation.
        None => line.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::replace;

    const FILE: &str = "class A:\n    def f(self):\n        x = 1  \n        return x\n\n    def g(self):\n        return 2\n";

    #[test]
    fn trailing_whitespace_differences_still_match() {
        let out = replace(FILE, "        x = 1\n        return x", "        x = 2\n        return x").unwrap();
        assert!(out.content.contains("        x = 2\n        return x\n\n    def g"));
        assert_eq!((out.first_line, out.last_line, out.reindented), (3, 4, false));
    }

    #[test]
    fn a_block_indented_wrong_is_matched_and_the_replacement_reindented() {
        let old = "def g(self):\n    return 2";
        let new = "def g(self):\n    value = 2\n    return value";
        let out = replace(FILE, old, new).unwrap();
        assert!(out.content.ends_with("    def g(self):\n        value = 2\n        return value\n"), "{}", out.content);
        assert!(out.reindented);
    }

    #[test]
    fn ambiguous_or_absent_text_does_not_match() {
        let twice = "a = 1\nb = 2\na = 1\n";
        assert!(replace(twice, "  a = 1", "a = 3").is_none());
        assert!(replace(FILE, "return 3", "return 4").is_none());
        assert!(replace(FILE, "\n\n", "x").is_none());
    }

    #[test]
    fn crlf_files_keep_their_line_endings() {
        let file = "one\r\n  two  \r\nthree\r\n";
        let out = replace(file, "  two\nthree", "  2\n3").unwrap();
        assert_eq!(out.content, "one\r\n  2\r\n3\r\n");
    }
}
