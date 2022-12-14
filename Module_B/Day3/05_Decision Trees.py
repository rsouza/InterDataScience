# Databricks notebook source
# MAGIC %md
# MAGIC # Decision Trees
# MAGIC 
# MAGIC In the previous notebook, you were working with the parametric model, Linear Regression. We could do some more hyperparameter tuning with the linear regression model, but we're going to try tree based methods and see if our performance improves.
# MAGIC 
# MAGIC ## ![Spark Logo Tiny](https://files.training.databricks.com/images/105/logo_spark_tiny.png) In this lesson you:<br>
# MAGIC  - Identify the differences between single node and distributed decision tree implementations
# MAGIC  - Get the feature importance
# MAGIC  - Examine common pitfalls of decision trees

# COMMAND ----------

# MAGIC %md
# MAGIC #### Importing modules and disabling MLflow  

# COMMAND ----------

import os
import mlflow
mlflow.autolog(disable=True)

# COMMAND ----------

# MAGIC %md
# MAGIC ### Setting the default database and user name  
# MAGIC ##### Substitute "renato" by your name in the `username` variable.

# COMMAND ----------

## Put your name here
username = "renato"

dbutils.widgets.text("username", username)
spark.sql(f"CREATE DATABASE IF NOT EXISTS dsacademy_embedded_wave3_{username}")
spark.sql(f"USE dsacademy_embedded_wave3_{username}")
spark.conf.set("spark.sql.shuffle.partitions", 40)

spark.sql("SET spark.databricks.delta.formatCheck.enabled = false")
spark.sql("SET spark.databricks.delta.properties.defaults.autoOptimize.optimizeWrite = true")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Reading Dataset

# COMMAND ----------

deltaPath = os.path.join("/", "tmp", username)    #If we were writing to the root folder and not to the DBFS
if not os.path.exists(deltaPath):
    os.mkdir(deltaPath)
    
print(deltaPath)

airbnbDF = spark.read.format("delta").load(deltaPath)

# COMMAND ----------

airbnbDF.limit(10).display()

# COMMAND ----------

# MAGIC %md
# MAGIC #### Checking Null Values in Spark Dataframe

# COMMAND ----------

for col in airbnbDF.columns:
  print(col, ":", airbnbDF.filter(f"{col} is NULL").count())

# COMMAND ----------

# MAGIC %md
# MAGIC #### Imputing Null Values  
# MAGIC [Python](https://spark.apache.org/docs/3.1.1/api/python/reference/api/pyspark.sql.DataFrame.fillna.html)  

# COMMAND ----------

airbnbDF.filter("host_total_listings_count is NULL").display()

# COMMAND ----------

airbnbDF = airbnbDF.fillna(0)

# COMMAND ----------

airbnbDF.filter("host_total_listings_count is NULL").display()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Train/Test Split
# MAGIC 
# MAGIC Let's use the same 80/20 split with the same seed as the previous notebook so we can compare our results apples to apples (unless you changed the cluster config!)

# COMMAND ----------

(trainDF, testDF) = airbnbDF.randomSplit([.8, .2], seed=42)

# COMMAND ----------

# MAGIC %md
# MAGIC ## How to Handle Categorical Features?
# MAGIC 
# MAGIC We saw in the previous notebook that we can use StringIndexer/OneHotEncoder/VectorAssembler or RFormula.
# MAGIC 
# MAGIC **However, for decision trees, and in particular, random forests, we should not OHE our variables.**
# MAGIC 
# MAGIC There is an excellent [blog](https://towardsdatascience.com/one-hot-encoding-is-making-your-tree-based-ensembles-worse-heres-why-d64b282b5769#:~:text=One%2Dhot%20encoding%20categorical%20variables,importance%20resulting%20in%20poorer%20performance) on this, and the essence is:
# MAGIC >>> "One-hot encoding categorical variables with high cardinality can cause inefficiency in tree-based methods. Continuous variables will be given more importance than the dummy variables by the algorithm, which will obscure the order of feature importance and can result in poorer performance."

# COMMAND ----------

from pyspark.ml.feature import StringIndexer

categoricalCols = [field for (field, dataType) in trainDF.dtypes if dataType == "string"]
indexOutputCols = [x + "Index" for x in categoricalCols]

stringIndexer = StringIndexer(inputCols=categoricalCols, outputCols=indexOutputCols, handleInvalid="skip")

# COMMAND ----------

# MAGIC %md
# MAGIC ## VectorAssembler
# MAGIC 
# MAGIC Let's use the VectorAssembler to combine all of our categorical and numeric inputs  
# MAGIC [Python](https://spark.apache.org/docs/latest/api/python/reference/api/pyspark.ml.feature.VectorAssembler.html)

# COMMAND ----------

from pyspark.ml.feature import VectorAssembler

# Filter for just numeric columns (and exclude price, our label)
numericCols = [field for (field, dataType) in trainDF.dtypes if ((dataType == "double") & (field != "price"))]
# Combine output of StringIndexer defined above and numeric columns
assemblerInputs = indexOutputCols + numericCols
vecAssembler = VectorAssembler(inputCols=assemblerInputs, outputCol="features")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Decision Tree
# MAGIC 
# MAGIC Now let's build a `DecisionTreeRegressor` with the default hyperparameters  
# MAGIC [Python](https://spark.apache.org/docs/latest/api/python/reference/api/pyspark.ml.regression.DecisionTreeRegressor.html)  

# COMMAND ----------

from pyspark.ml.regression import DecisionTreeRegressor

dt = DecisionTreeRegressor(labelCol="price")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Fit Pipeline
# MAGIC 
# MAGIC The following cell is expected to error, but we subsequently fix this.

# COMMAND ----------

from pyspark.ml import Pipeline

# Combine stages into pipeline
stages = [stringIndexer, vecAssembler, dt]
pipeline = Pipeline(stages=stages)

# Uncomment to perform fit
#pipelineModel = pipeline.fit(trainDF)

# COMMAND ----------

# MAGIC %md
# MAGIC ## maxBins
# MAGIC 
# MAGIC What is this parameter [maxBins](https://spark.apache.org/docs/latest/api/python/pyspark.ml.html#pyspark.ml.regression.DecisionTreeRegressor.maxBins)? Let's take a look at the PLANET implementation of distributed decision trees to help explain the `maxBins` parameter.

# COMMAND ----------

# MAGIC %md
# MAGIC <img src="https://files.training.databricks.com/images/DistDecisionTrees.png" height=500px>

# COMMAND ----------

# MAGIC %md
# MAGIC In Spark, data is partitioned by row. So when it needs to make a split, each worker has to compute summary statistics for every feature for  each split point. Then these summary statistics have to be aggregated (via tree reduce) for a split to be made.
# MAGIC 
# MAGIC Think about it: What if worker 1 had the value `32` but none of the others had it. How could you communicate how good of a split that would be? So, Spark has a maxBins parameter for discretizing continuous variables into buckets, but the number of buckets has to be as large as the categorical variable with the highest cardinality.

# COMMAND ----------

# MAGIC %md
# MAGIC Let's go ahead and increase maxBins to `60`.

# COMMAND ----------

dt.setMaxBins(60)

# COMMAND ----------

# MAGIC %md
# MAGIC Take two.

# COMMAND ----------

pipelineModel = pipeline.fit(trainDF)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Feature Importance
# MAGIC 
# MAGIC Let's go ahead and get the fitted decision tree model, and look at the feature importance scores.

# COMMAND ----------

dtModel = pipelineModel.stages[-1]
display(dtModel)

# COMMAND ----------

dtModel.featureImportances

# COMMAND ----------

# MAGIC %md
# MAGIC ## Interpreting Feature Importance
# MAGIC 
# MAGIC Hmmm... it's a little hard to know what feature 4 vs 11 is. Given that the feature importance scores are "small data", let's use Pandas to help us recover the original column names.

# COMMAND ----------

import pandas as pd

featuresDF = pd.DataFrame(list(zip(vecAssembler.getInputCols(), dtModel.featureImportances)), columns=["feature", "importance"])
featuresDF

# COMMAND ----------

# MAGIC %md
# MAGIC ## Why so few features are non-zero?
# MAGIC 
# MAGIC With SparkML, the default `maxDepth` is 5, so there are only a few features we could consider (we can also split on the same feature many times at different split points).
# MAGIC 
# MAGIC Let's use a Databricks widget to get the top-K features.

# COMMAND ----------

dbutils.widgets.text("topK", "5")
topK = int(dbutils.widgets.get("topK"))

topFeatures = featuresDF.sort_values(["importance"], ascending=False)[:topK]["feature"].values
print(topFeatures)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Scale Invariant
# MAGIC 
# MAGIC With decision trees, the scale of the features does not matter. For example, it will split 1/3 of the data if that split point is 100 or if it is normalized to be .33. The only thing that matters is how many data points fall left and right of that split point - not the absolute value of the split point.
# MAGIC 
# MAGIC This is not true for linear regression, and the default in Spark is to standardize first. Think about it: If you measure shoe sizes in American vs European sizing, the corresponding weight of those features will be very different even those those measures represent the same thing: the size of a person's foot!

# COMMAND ----------

# MAGIC %md
# MAGIC ## Apply model to test set

# COMMAND ----------

predDF = pipelineModel.transform(testDF)

display(predDF.select("features", "price", "prediction").orderBy("price", ascending=False))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Pitfall
# MAGIC 
# MAGIC What if we get a massive Airbnb rental? It was 20 bedrooms and 20 bathrooms. What will a decision tree predict?
# MAGIC 
# MAGIC It turns out decision trees cannot predict any values larger than they were trained on. The max value in our training set was $10,000, so we can't predict any values larger than that.

# COMMAND ----------

from pyspark.ml.evaluation import RegressionEvaluator

regressionEvaluator = RegressionEvaluator(predictionCol="prediction", labelCol="price", metricName="rmse")

rmse = regressionEvaluator.evaluate(predDF)
r2 = regressionEvaluator.setMetricName("r2").evaluate(predDF)
print(f"RMSE is {rmse}")
print(f"R2 is {r2}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Uh oh!
# MAGIC 
# MAGIC This model is way worse than the linear regression model, and it's even worse than just predicting the average value.
# MAGIC 
# MAGIC In the next few notebooks, let's look at hyperparameter tuning and ensemble models to improve upon the performance of our single decision tree.

# COMMAND ----------

# MAGIC %md
# MAGIC Code modified and enhanced from 2020 Databricks, Inc. All rights reserved.<br/>
# MAGIC Apache, Apache Spark, Spark and the Spark logo are trademarks of the <a href="http://www.apache.org/">Apache Software Foundation</a>.<br/>
# MAGIC <br/>
# MAGIC <a href="https://databricks.com/privacy-policy">Privacy Policy</a> | <a href="https://databricks.com/terms-of-use">Terms of Use</a> | <a href="http://help.databricks.com/">Support</a>
