/** A provider's and a provider's model's addresses, built only here.
 *
 *  `/providers/<id>` and `/providers/<id>/models/<model>`: the id is encoded
 *  whole, the model segment by segment, because a model id's own `/` is part of
 *  the path the `models/*` splat reads back. The server builds the same grammar
 *  for Housekeeping's links (`routes/todo.py` `_model_rates_href`).
 *
 *  A leaf with no imports, so a cost report can link a model's rates without
 *  importing the providers page.
 */

export const providerPath = (id: string) => `/providers/${encodeURIComponent(id)}`;

export const modelPath = (id: string, model: string) =>
  `${providerPath(id)}/models/${model.split("/").map(encodeURIComponent).join("/")}`;

/** The `?edit=…` value that opens a model's facts on its rates. */
export const EDIT_RATES = "rates";

/** A model's facts on its provider, opened on the rates form. */
export const modelRatesPath = (id: string, model: string) =>
  `${modelPath(id, model)}?edit=${EDIT_RATES}`;
