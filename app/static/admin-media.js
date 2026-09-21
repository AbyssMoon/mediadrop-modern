(() => {
  const form = document.querySelector("form[data-media-create]");
  if (!form) return;

  const title = form.querySelector('input[name="title"]');
  const slug = form.querySelector('input[name="slug"]');
  if (!title || !slug) return;

  // Keep this intentionally small. The server runs Unidecode again on save,
  // so this is only a convenient live preview for common Latin/Cyrillic titles.
  const cyrillic = {
    а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "yo", ж: "zh",
    з: "z", и: "i", й: "y", к: "k", л: "l", м: "m", н: "n", о: "o",
    п: "p", р: "r", с: "s", т: "t", у: "u", ф: "f", х: "h", ц: "c",
    ч: "ch", ш: "sh", щ: "sch", ъ: "", ы: "y", ь: "", э: "e", ю: "yu",
    я: "ya", і: "i", ї: "yi", є: "ye", ґ: "g", ў: "u"
  };

  const makeSlug = (value) => Array.from(value.toLowerCase())
    .map((char) => cyrillic[char] ?? char)
    .join("")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 45);

  let manuallyEdited = slug.value.trim() !== "";

  title.addEventListener("input", () => {
    if (!manuallyEdited) slug.value = makeSlug(title.value);
  });

  slug.addEventListener("input", () => {
    manuallyEdited = slug.value.trim() !== "";
  });

  if (!slug.value) slug.value = makeSlug(title.value);
})();
