import os
from xmlrpc.client import Fault, ServerProxy

from flask import Flask, redirect, render_template, request, url_for
from werkzeug.contrib.cache import SimpleCache

app = Flask(__name__)
cache = SimpleCache(default_timeout=1)


SERVER = os.getenv("ODOO_SERVER", "http://127.0.0.1:8069")
DATABASE = os.getenv("ODOO_DB", "odoodb")
USERNAME = os.getenv("ODOO_USERNAME", "admin")
PASSWORD = os.getenv("ODOO_PASSWORD", "admin")
CONFIG = dict(
    server=SERVER,
    database=DATABASE,
    username=USERNAME,
)


def query_odoo(model, operation, param, opts=None):
    """Generic communication with Odoo

    :param model: the Odoo model name
    :param operation: what action to perform
    :param param: the parameters for the action
    :param opts: the options for the action
    :returns: the Odoo results
    """
    app.logger.debug("%s %s %s", SERVER, DATABASE, USERNAME)

    # Check if the result was already retrieved earlier
    key = f"{model}-{operation}-{param}-{opts}"
    rv = cache.get(key)
    if rv:
        app.logger.debug(f"returning cached results for {key}")
        return rv
    else:
        app.logger.debug(f"no cached results for {key}")

    common = ServerProxy(f"{SERVER}/xmlrpc/2/common")
    models = ServerProxy(f"{SERVER}/xmlrpc/2/object")

    user_id = common.login(DATABASE, USERNAME, PASSWORD)
    try:
        results = models.execute_kw(
            DATABASE,
            user_id,
            PASSWORD,
            model,
            operation,
            [param],
            {} if opts is None else opts,
        )
        cache.set(key, results, timeout=5 * 60)
        return results
    except Fault as err:
        app.logger.debug("Error handling %s", err)


def get_fields(model_id):
    """Read the Odoo fields of a model

    :param model_id: the ID of the Odoo model
    :return: the fields of the model
    """
    return query_odoo(
        "ir.model.fields", "search_read", [("model_id", "=", model_id)], {}
    )


def create_model_query(model_names):
    """Construct the Odoo domain filter
    :param model_names: the requested list of Odoo model names
    :return: the Odoo domain filter

     >>> create_model_query([])
     []
     >>> create_model_query(['sale.order'])
     [('model', '=', 'sale.order')]
     >>> create_model_query(['bus.bus', 'edi.edit'])
     ['|', ('model', '=', 'bus.bus'), ('model', '=', 'edi.edit')]
     >>> create_model_query(['bus.bus', 'edi.edit', 'ir.cron'])
     ['|', ('model', '=', 'bus.bus'), '|', ('model', '=', 'edi.edit'), ('model', '=', 'ir.cron')]
    """
    query = [("model", "=", item) for item in model_names]
    if len(model_names) < 2:
        return query

    combined_query = []
    for item in query[:-1]:
        combined_query.extend(["|", item])
    combined_query.append(query[-1])
    return combined_query


def get_models(model_names):
    """Retrieve Odoo models

    :param model_names: a list of Odoo model names
    :return: a list of dictionaries describing Odoo models
    """
    query = create_model_query(model_names)
    app.logger.debug(query)
    return query_odoo("ir.model", "search_read", query)


def get_models_with_relations(model_names):
    """Get the specified models and their relationships"""
    results = get_models(model_names)

    relations = []
    models = []
    for model in results:
        fields = get_fields(model["id"])
        models.append((model, fields))
        for field in fields:
            if "relation" in field and field["relation"]:
                if field["relation"] in model_names:
                    relations.append(field)

    return models, relations


@app.route("/")
def main():
    """Show the main webpage"""
    return render_template("main.html", config=CONFIG)


@app.route("/delete/<model_name>/<int:row_id>")
def delete_row(model_name, row_id):
    """Delete a record within an Odoo model"""
    app.logger.debug(f"{model_name}")
    results = query_odoo(model_name, "unlink", [row_id])
    app.logger.error(results)
    return redirect(url_for("view_model", name=model_name))


@app.route("/model/<model_name>")
def view_model(model_name=None):
    """Show meta info about the records in an Odoo model"""
    opts = dict(
        fields=[
            "name",
            "create_uid",
            "create_date",
            "write_uid",
            "write_date",
            "model_name",
        ],
        limit=10,
    )
    app.logger.debug(f"{model_name}")
    results = query_odoo(model_name, "search_read", [], opts)
    return render_template("data_list.html", objects=results, model_name=model_name)


@app.route("/list/")
def list_models():
    """List all Odoo models"""
    results = query_odoo("ir.model", "search_read", [])
    return render_template("model_list.html", objects=results, length=len(results))


@app.route("/fields/<int:model_id>")
def list_fields(model_id):
    """List all the fields within a model"""
    query = [("model_id", "=", model_id)]
    app.logger.debug(query)
    results = query_odoo("ir.model.fields", "search_read", query)
    app.logger.debug(results)
    return render_template("field_list.html", objects=results, length=len(results))


@app.route("/data/<model_name>")
def view_data(model_name):
    """Show the data within a model"""
    results = query_odoo(model_name, "search_read", [])
    app.logger.debug(results)
    return render_template("all_data_list.html", objects=results, length=len(results))


@app.route("/detail/<model_name>/<int:row_id>")
def view_details(model_name, row_id):
    """Show the data of the specific row of a model

    :param model_name: the Odoo model name
    :param row_id: the ID of the record in the model
    :return: a page showing the record information
    """
    query = [("id", "=", row_id)]
    models, relations = get_models_with_relations([model_name])
    results = query_odoo(model_name, "search_read", query)
    app.logger.debug(results)
    return render_template(
        "detail.html",
        object=results[0],
        model=models[0][0],
        fields=models[0][1],
        relations=relations,
    )


@app.route("/plantuml", methods=["POST"])
def view_plantuml():
    """Return a PlantUML file for graphing the models"""
    model_names = []
    for k, v in request.form.items():
        if v == "on":
            model_names.append(k)
    results, relations = get_models_with_relations(model_names)
    return (
        render_template("plantuml.txt", objects=results, relations=relations),
        200,
        {"Content-Type": "text/plain"},
    )


if __name__ == "__main__":
    app.run()
