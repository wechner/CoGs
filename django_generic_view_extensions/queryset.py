'''
Django Generic View Extensions

QuerySet Extensions

'''
from django.db import connection

def get_SQL(queryset, explain=False):
    '''
    A workaround for a bug in Django which is reported here (several times):
        https://code.djangoproject.com/ticket/30132
        https://code.djangoproject.com/ticket/25705
        https://code.djangoproject.com/ticket/25092
        https://code.djangoproject.com/ticket/24991
        https://code.djangoproject.com/ticket/17741
        
    that should be documented here:
        https://docs.djangoproject.com/en/2.1/faq/models/#how-can-i-see-the-raw-sql-queries-django-is-running
    but isn't.
    
    The work around was published by Zach Borboa here:
        https://code.djangoproject.com/ticket/17741#comment:4
        
    :param query:
    '''
    if explain:
        sql, params = queryset.query.sql_with_params()
        cursor = connection.cursor()
        cursor.execute('EXPLAIN ' + sql, params)
        return cursor.db.ops.last_executed_query(cursor, sql, params).replace("EXPLAIN ", "", 1)
    else:
        # We don't want to execute a query in this case but find the SQL reliablyf rom Django
        
        # We can get SQL and params from the query compiler 
        sql, params = queryset.query.get_compiler(using=queryset.db).as_sql()
        
        # And this is used when excuted as described here:
        #  https://docs.djangoproject.com/en/2.2/topics/db/sql/#passing-parameters-into-raw
        #
        # The key note being:           
        #     params is a list or dictionary of parameters. 
        #     You’ll use %s placeholders in the query string for a list, 
        #     or %(key)s placeholders for a dictionary (where key is replaced 
        #     by a dictionary key, of course)
        #
        # Which is precisely how Python2 standard % formating works.        
        return sql % params        

def wrap_filter(queryset, sql_where_crtiteria):
    '''
    A sad shortfall in Django at present is that we cannot filter on Window functions used as 
    annotations.
    
    See:
        https://code.djangoproject.com/ticket/30104
        https://code.djangoproject.com/ticket/28333
            "the window function expression should be wrapped in an inner query, 
            and the filtering should be done in an outer query."
            
    Here  is our workaround untilt that is fixed in Django, namely we wrap a select/where 
    around the exisitng queryset explicitly.
    
    Alas in so doing we turn it into a raw queryset.
    
    First we need the SQL from the existing query. Many on-line sources seem to recommend 
    str(qs.query) but this does not return reliable SQL! A bug in Django which is much 
    discussed:
       https://code.djangoproject.com/ticket/30132
       https://code.djangoproject.com/ticket/25705
       https://code.djangoproject.com/ticket/25092
       https://code.djangoproject.com/ticket/24991
       https://code.djangoproject.com/ticket/17741
    
    So we need to dig deeper in to Django and fetch it from the query compiler.
    
    TODO: neighbours.py should use this too.
    '''
    
    # str(queryset.query) is woefully inadequate here, but we can get reliable
    # SQL from the query compiler as follows.
    sql, params = queryset.query.get_compiler(using=queryset.db).as_sql()
    
    # Now we can wrap that SQL in a new SELECT/WHERE statement to secure our filter
    sql = "SELECT * FROM ({}) ao WHERE {}".format(sql, sql_where_crtiteria)
    
    # And we can return the raw queryset we've built
    return queryset.model.objects.raw(sql, params)


def top(queryset, number_of_rows, use_slice=True):
    '''
    A small queryset wrapper to get the top rows of a queryset without slicing it. 
    
    Slicing may causes evaluation. 
    
    https://docs.djangoproject.com/en/2.2/ref/models/querysets/#when-querysets-are-evaluated
    
    Though to be honest it mostly doesnt ans slicing with [:number_of_rows] may well,
    if it's the last operation, also return an unevaluated QuerySet. 
    
    This method:
    
    a) is postgresql specific, no gurantee it will work on another database engine
    b) produces a raw queryset adding a LIMIT specifier.
    
    As per the example here:
    
        https://docs.djangoproject.com/en/2.2/ref/models/expressions/#subquery-expressions
    
    It seems that slicing if done prudently does the same but must be the last 
    operation on a queryset. This may of course also be true of a raw queryset,
    
    TODO: Can a raw querset be filtered further and what is the SQL produced? If
    the answer is yes, and subsequent filters apply to a wrapped queryset in the
    manner of wrap_filter() above or as asked for here:
    
        https://code.djangoproject.com/ticket/28333
        
    then this manner of taking the top slice is better (because it need not
    then, be the last operation on a queryset).
    '''

    if use_slice:
        return queryset[:number_of_rows] 
    else:    
        # str(queryset.query) is woefully inadequate here, but we can get reliable
        # SQL from the query compiler as follows.
        sql, params = queryset.query.get_compiler(using=queryset.db).as_sql()
        
        # Now we can wrap that SQL in a new SELECT/WHERE statement to secure our filter
        sql = "{}\nLIMIT {}".format(sql, number_of_rows)
        
        # And we can return the raw queryset we've built
        return queryset.model.objects.raw(sql, params)
