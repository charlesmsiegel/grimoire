/** @type {import("eslint").Rule.RuleModule} */
const rule = {
  meta: {
    type: "problem",
    docs: {
      description:
        "Disallow bespoke delete/remove buttons; route deletes through CardIconBar's deleteAction.",
    },
    schema: [],
    messages: {
      bespoke:
        "Bespoke delete control. Render deletes through CardIconBar (deleteAction) instead of a raw <{{tag}}>.",
    },
  },
  create(context) {
    const filename = context.filename ?? context.getFilename();
    const norm = String(filename).replace(/\\/g, "/");
    // Exempt the sanctioned implementation and confirm dialogs.
    if (/\/CardIconBar\.tsx$/.test(norm) || /(Dialog|Confirm)[^/]*\.tsx?$/.test(norm)) {
      return {};
    }

    const TAGS = new Set(["button", "a", "Link"]);
    const isDeleteText = (s) => /^\s*(delete|remove)\b/i.test(s) || s.includes("🗑");

    return {
      JSXElement(node) {
        const open = node.openingElement;
        if (open.name.type !== "JSXIdentifier" || !TAGS.has(open.name.name)) return;

        let hit = false;
        for (const attr of open.attributes) {
          if (attr.type !== "JSXAttribute" || !attr.name) continue;
          const attrName = attr.name.name;
          const v = attr.value;
          const str =
            v && v.type === "Literal" && typeof v.value === "string"
              ? v.value
              : v &&
                  v.type === "JSXExpressionContainer" &&
                  v.expression.type === "Literal" &&
                  typeof v.expression.value === "string"
                ? v.expression.value
                : null;
          if (str === null) continue;
          if (attrName === "className" && /delete/i.test(str)) hit = true;
          if ((attrName === "aria-label" || attrName === "title") && isDeleteText(str)) hit = true;
        }
        if (!hit) {
          // Trash emoji as a direct text child.
          for (const child of node.children) {
            if (child.type === "JSXText" && child.value.includes("🗑")) hit = true;
          }
        }
        if (hit) {
          context.report({ node: open, messageId: "bespoke", data: { tag: open.name.name } });
        }
      },
    };
  },
};

export default rule;
